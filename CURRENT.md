# 数字分身 · 当前状态

需求以 [PRD.md](PRD.md) 为准（v1.1，2026-10-26 已拍板）。本文件只记进度和唯一的下一步，新的记录写在最上面。

证据目录：`~/Library/Application Support/DigitalTwin/evidence/<批次目录>/`（备份含密钥，权限 700/600，不进仓库）。

---

## 2026-10-29 晚 · B2.2 G1–G3 独立复核：G1 / G2 通过，G3 有条件通过，停在 G3

- 时间：开工 23:20
- **来由**：常浩把 ChatGPT 的 G1–G3 独立复核结论发来（原文 [docs/reviews/2026-10-29-ChatGPT-B2.2-G1至G3独立复核原文.md](docs/reviews/2026-10-29-ChatGPT-B2.2-G1至G3独立复核原文.md)）。ChatGPT 连到本机核对了仓库、PR、安装副本、真实工单、回执与运行开关，没发现提前建库或启用调度。
- **正式裁定**：B2.2 G1–G3 受控部署验收通过（G3 保留一项补验）。**B22-G3：手工派单核心回归通过；运行中 `observe` 体征验证未完成，批准顺延至 G4 首次手工单补验。G4 该项不通过，不得继续 G5。** 不回退安装、不重开代码返修。
- 测试：本轮只记录复核结论，未跑测试、未动安装目录与派单目录。
- 本轮新坑：无。
- **G4 启动前的必要条件**（复核第三节）：
  - 单独批准正式库初始化、备份与对账，以及一张用于观测的只读手工单。注意：G4 名为“只读观测”，但准备阶段的 `init → backup → reconcile` 会写正式库，必须单独授权，不能用“只读观测”概括。
  - 按 B2.1 接入清单执行 `init → backup → reconcile`，记录初始库版本和历史单导入结果；备份、检查 SQLite 版本与文件权限；历史单数量按当时实际目录复核，不沿用旧快照。
  - 连续对账两次，第二次不得产生重复 Run 或意外冲突。
  - **首项必测**：工单处于 `running` 时，`observe` 必须返回该单并判 `alive`，同时核对进程身份；工单结束后再观测，不再当作活动车间，状态库与台账无矛盾。
  - `DT_SCHED_ENABLE`、`DT_STATE_NOTIFY` 保持关闭；不建 cron、不自动开单、不自动确认报告、不发真实取消信号；不为 G4 提前配 `owner_chat_id`。
  - 正式库现在不存在，首次初始化可直接建 v3；若执行前发现已有 v2 库，必须先备份再迁移。
- 没做：正式库未建；G4–G7 未批未做；D6 的 G4 行尚未写入“运行中 observe 为首项必测”，在 G4 施工计划里一并更新。
- 下一步：等常浩明确授权 G4；授权后先出 G4 施工计划（含上面各项与回退），确认后再动正式库。

---

## 2026-10-29 晚 · B2.2 手工派单回归（G3）：只读小单 `D20261029-1` 走通，未启用调度

- 时间：开工 23:04
- **来由**：常浩说「批准 G3」，即批准 D6 的 B22-G3（一张只读小单走 `new → run → list`）；G4–G7 未批。
- **做了什么**：用安装版 `~/.hermes/scripts/dispatch.py`（`/usr/bin/python3`）开单 `D20261029-1`（数字分身 · claude · 只读，任务：列出 `hermes/scripts/` 下的 `.py` 文件），跑单、`list`；再用隔离库（会话临时目录里新建的空库，不是正式库）跑 `twin_scheduler.py observe` 读真实派单目录。
- 测试：
  - `new` 退出码 0；order.json 字段 `no / project / tool / mode / status / created / cwd / updated`，与以前的只读单相同，没有 `sched_key`、目录里没有 `sched_key` 文件；`~/.hermes/dispatch/.dispatch.lock` 首次出现（空文件）。
  - `run` 退出码 0，`跑完：done`，18 秒；运行中 order.json 为 `running` 并带 `run_started / run_host / dispatch_pid`，车间写了 `out/worker.json`（`pid = pgid = sid`，自成进程组）；回执 `exit_code 0`、`head_before = head_after = b4e0747`、无提交、无未提交改动、各护栏 false；回执字段与 10-26 的只读单相比只多 `note_remote_moved`（M3 批 5 加的，与 B2.2 无关）。车间答 12 个文件，与实际一致。
  - `list` 退出码 0，该单 `done`。
  - `observe` 退出码 0，“没有活动的单”（单已跑完）。**照实**：跑单期间那次 `observe` 指向的隔离库还没建，只读打开拒绝建库，退出码 1（`observe-during-run-no-db.json`），所以“跑单中用 observe 看到该单体征”这一项没验到；运行中的身份字段是直接读 order.json / worker.json 核对的。
  - 证据 `~/Library/Application Support/DigitalTwin/evidence/b2.2-g3-20261029/`（`new.json`、`run.json`、`list.json`、`observe.json`、`identity-fields.txt`、`task.md`）。
- 本轮新坑：无。（事实：`twin_scheduler.py` 的只读命令不会替你建库，隔离库要先显式建好再观察。）
- 没做（照实）：正式库未建；`DT_SCHED_ENABLE`、cron、自动开单、报告投递、真实取消信号都没开；网关 / 技能 / SOUL / config / `.env` 未改；主目录 main 仍是 `b4e0747`、工作区干净。
- 下一步：B22-G4（正式库观察）待常浩单独批准；如需补验“跑单中 observe 能看到体征”，可在 G4 建库后的第一张手工单上顺带核对。G5–G7 仍按 D6 逐项单独批。

---

## 2026-10-29 晚 · B2.2 安装（G2）：四个脚本已装到 `~/.hermes/scripts/`，未启用

- 时间：开工 23:00
- **来由**：常浩说「批准 G2 安装」，即批准 D6 的 B22-G2（只复制脚本、不启用）；G3–G7 未批。
- **做了什么**：
  - 安装前：主目录 main `3cb3b83`、`hermes/scripts` 无改动；没有在跑的派单或车间进程，台账里没有 new / queued / running 的单；安装版 `self_check.py` 报的正好是这四个文件（3 个不一致、`twin_scheduler.py` 缺失）。
  - 备份：旧副本 `twin_state.py`（sha256 `6adc2d49b787…`）、`dispatch.py`（`e5c9eaf5b497…`）、`run_worker.py`（`ad650618509c…`）复制到证据目录 `backup-before-install/`（700 / 600），逐个 `cmp` 一致；`twin_scheduler.py` 原本不存在。
  - 安装：从 main 复制 `twin_scheduler.py`（新，644）、`twin_state.py`、`dispatch.py`、`run_worker.py` 到 `~/.hermes/scripts/`，逐个 `cmp` 与 main 一致，且与合并提交 `b4c350c` 无差异。
- 测试：`/usr/bin/python3`（3.9.6）与 `python3`（3.14.5）在安装目录都能导入四个模块（库结构版本 3），退出码 0；安装版 `self_check.py` 静默、退出码 0。证据 `~/Library/Application Support/DigitalTwin/evidence/b2.2-install-20261029/`（`before-install-sha256.txt`、`post-install-checks.txt`、`backup-before-install/`）。
- 本轮新坑：无。
- 没做（照实）：正式库 `twin-state.db` 未建；`~/.hermes/dispatch` 下还没有 `.dispatch.lock`、`.sched-tasks`（下一次手工派单才会建锁文件）；`.env` 无 `DT_SCHED_ENABLE`；没建 cron；自动开单、报告投递、真实取消信号都没开；网关 / 技能 / SOUL / config 未改、未重启。worktree `~/Sandbox/worktrees/digital-twin-b2-2` 与本地分支先保留。
- 安装后对手工派单的变化（D6 第 2 节）：`new / run` 多拿一把派单锁；`run` 的车间自成进程组，order.json 多 `run_started / run_host / dispatch_pid`，车间写 `out/worker.json`；同仓库两单同时 `run` 时后到的排队（`busy: true`）。不带 `--sched-key` 的 `new` 输出与字段不变。
- 回退：`cp -p <证据目录>/backup-before-install/{twin_state.py,dispatch.py,run_worker.py} ~/.hermes/scripts/`，删除 `~/.hermes/scripts/twin_scheduler.py`；之后 09:50 自检会报不一致，属回退后的预期。
- 下一步：B22-G3 手工派单回归（一张只读小单走 `new → run → list`，核对输出不变与新身份字段），待常浩单独批准；G4–G7 仍按 D6 逐项单独批。

---

## 2026-10-29 晚 · B2.2 合并（G1）：已合并 `b4c350c`，未安装、未启用

- 时间：开工 22:50
- **来由**：ChatGPT 最终独立验收（原文 [docs/reviews/2026-10-29-ChatGPT-B2.2最终独立验收报告原文.md](docs/reviews/2026-10-29-ChatGPT-B2.2最终独立验收报告原文.md)）：同键重复开单 P0 已修复，技术验收通过，B22-G1 可进入合并审批；评审通过本身不是合并授权。常浩随后说「批准合并」，即批准 G1；G2–G7 未批。
- **做了什么**：
  - 合并前复核：PR #30 HEAD 仍是已验收的 `854adcd`，本机与远端一致、工作树干净；`origin/main...HEAD` = 0 / 9，无需变基；主目录在 main、无未推提交、与远端一致；PR 差异新增行密钥 / 身份值扫描无命中；`OPEN` `MERGEABLE` `CLEAN`。
  - G1：`gh pr merge 30 --squash --match-head-commit 854adcd…` → 合并提交 `b4c350c`，文件树与 `854adcd` 完全一致（`d40a79f`）；Issue #29 自动关闭；远端分支自动删除；主目录 `pull --ff-only` 拉齐。
- 测试：合并前在 `854adcd` 上 `/usr/bin/python3 -B -m unittest discover -s hermes/scripts/tests`（3.9.6）`Ran 277 tests` `OK` 退出码 0；`python3 …`（3.14.5）`Ran 277 tests` `OK` 退出码 0；无残留测试进程。证据 `~/Library/Application Support/DigitalTwin/evidence/b2.2-merge-20261029/`（`pre-merge-python3-3.9.txt`、`pre-merge-python3-3.14.txt`）。
- 本轮新坑：无。
- 没做（照实）：G2 未安装（`~/.hermes/scripts/` 里没有 `twin_scheduler.py`，`twin_state.py` / `dispatch.py` / `run_worker.py` 仍是旧安装副本）；正式库 `twin-state.db` 未建；`DT_SCHED_ENABLE`、cron、自动开单、报告投递、真实取消信号都没开；网关 / 技能 / SOUL / `.env` 未改。worktree `~/Sandbox/worktrees/digital-twin-b2-2` 与本地分支先保留。合并后到安装前，09:50 工程自检会报这四个脚本缺失或与 main 不一致，属预期。
- 回退：代码回退走 `git revert b4c350c` 的 PR；本轮没有改动安装目录，无需恢复副本。
- 下一步：B22-G2 安装（只复制脚本，不建正式库、不开 `DT_SCHED_ENABLE` / cron / 自动开单），待常浩单独批准；G3–G7 仍按 D6 逐项单独批。

---

## 2026-10-29 晚 · B2.2 最后定向返修：同键重复开单（[PR #30](https://github.com/Natsukashii1997/digital-twin/pull/30) 同一分支，待 ChatGPT 最后复核）

- 时间：开工 22:00
- **来由**：ChatGPT 返修终审（原文 [docs/reviews/2026-10-29-ChatGPT-B2.2返修终审报告原文.md](docs/reviews/2026-10-29-ChatGPT-B2.2返修终审报告原文.md)、任务书 [docs/reviews/2026-10-29-ChatGPT-B2.2同键重复开单返修任务书原文.md](docs/reviews/2026-10-29-ChatGPT-B2.2同键重复开单返修任务书原文.md)）：F1 / F2 主要返修通过；新发现合并前 P0——同一个仍是 claimed 的关联键能开出两张单。按源码核实属实。常浩确认：既没标记也没台账的目录不算占用；台账读不了时拒绝开调度单，不改 `save()`。
- **做了什么**（详见交付报告第 10 节）：`dispatch.py` 新增 `check_sched_key_unused`，在同一把派单锁里、核对预留之后、编号建目录之前扫派单根目录直接子目录，已有同键标记或台账（含半截目录）就拒绝并列出单号，读不清也拒绝；不删、不关联。新增 `DupKeyTests` 4 项；D1 / D2 / 交付报告 / tests README 同步；终审原文与任务书入库。`twin_scheduler.py`、`twin_state.py`、F2 未改。
- 测试：新增 4 项放到 `9709be7` 上两套 Python 各 `Ran 4 tests` `FAILED (failures=4)` exit 1；修后 HEAD `ce474ba` 全量 `/usr/bin/python3 -B -m unittest discover -s hermes/scripts/tests`（3.9.6）`Ran 277 tests` `OK` exit 0，`python3 …`（3.14.5）`Ran 277 tests` `OK` exit 0；`git diff --check` 无输出。证据 `~/Library/Application Support/DigitalTwin/evidence/b2.2-rework2-20261029/`。
- 本轮新坑：无。（事实：状态库里预留仍是 claimed 不等于台账里还没开过单，幂等检查要在建目录的同一把锁里看台账本身。）
- 没做：未合并、未安装、未建正式库、未建 cron、未改 `.env` / 技能 / SOUL / config / 网关、未操作真实工单或发信号。
- 下一步：交 ChatGPT 最后复核；通过后进入 B22-G1 合并审批，G2–G7 仍各自单独授权。

---

## 2026-10-29 晚 · B2.2 合并前定向返修 F1 / F2（[PR #30](https://github.com/Natsukashii1997/digital-twin/pull/30) 同一分支，待复核）

- 时间：开工 21:00
- **来由**：ChatGPT 独立评审「有条件通过、暂不批准合并」（原文 [docs/reviews/2026-10-29-ChatGPT-B2.2评审原文.md](docs/reviews/2026-10-29-ChatGPT-B2.2评审原文.md)、任务书 [docs/reviews/2026-10-29-ChatGPT-B2.2返修任务书原文.md](docs/reviews/2026-10-29-ChatGPT-B2.2返修任务书原文.md)）：F1（P0）预留作废后迟到的旧关联键仍能开单、任务重新可派；F2（P1）报告打印即标记已报，投递失败会吞掉提醒。按源码核实属实；常浩确认 dispatch 开调度单时可读状态库、`report` 改为不自动标记、一次做完再交。
- **做了什么**（详见交付报告第 9 节）：
  - F1：执行器拉开单子进程前持久写“准备开单”标记，作废只认“没标记”或“子进程登记过且已退出”，否则 unsafe；`dispatch new --sched-key` 在派单锁里、建目录前只读状态库核对预留仍 claimed、项目一致，否则拒绝；新增游离单识别（决策 unsafe、报告 error、`recover` 退出码 1，不删不关联）。手工开单不读状态库、行为不变；锁顺序仍是派单锁 → 状态库。
  - F2：`report` 只读、不标记；新增 `ack --fingerprint` 与 `deliver()`，只有确认送达才标记；D6 的 G5 改为可信投递确认接通前不得启用。
  - 文档：D1 / D2 / D6 / 交付报告、tests README；评审原文与任务书入库。
- 测试：新增 14 项先在 `4154ef0` 上两套 Python 各 `Ran 14 tests` `FAILED (failures=6, errors=6)`；返修后各 `Ran 14 tests` `OK`；全量 `python3 -B -m unittest discover -s hermes/scripts/tests`（3.14.5）`Ran 273 tests` `OK` 退出码 0；`/usr/bin/python3 …`（3.9.6）`Ran 273 tests` `OK` 退出码 0；`git diff --check` 无输出。证据 `~/Library/Application Support/DigitalTwin/evidence/b2.2-rework-20261029/`。
- 本轮新坑：无。（事实：“子进程身份没登记”不能推出“子进程没启动”，崩溃窗口里要先落“准备开单”标记再拉起；脚本 stdout 只是待投递内容，不能当送达确认。）
- 没做：未合并、未安装、未建正式库、未建 cron、未改 `.env` / 技能 / SOUL / config / 网关、未操作真实工单；自动开单、真实取消信号、报告定时任务仍关闭。
- 下一步：返修交 ChatGPT 复核；通过后按 D6 逐项批 B22-G1 合并 → G2 安装。

---

## 2026-10-29 晚 · B2.2 持久调度与跨会话恢复（Issue #29，[PR #30](https://github.com/Natsukashii1997/digital-twin/pull/30) 待评审，未合并、未安装）

- 时间：开工 17:29
- **来由**：ChatGPT《Hermes B2.2 持久调度与跨会话恢复 正式施工任务书 V1.0》（原文 [docs/reviews/2026-10-29-ChatGPT-B2.2施工任务书原文.md](docs/reviews/2026-10-29-ChatGPT-B2.2施工任务书原文.md)）。常浩定三项：允许 dispatch.py 做锁 / 进程身份 / `--sched-key` 最小改动；批 2 后停一次；SIGTERM 只对测试受控子进程真发，真实路径只出建议。分支 `claude/29-b2-2-scheduler`，worktree `~/Sandbox/worktrees/digital-twin-b2-2`；起点 PR #28 HEAD `ecbb50a`，#28 合并后变基到 main `b0d05a4`，主目录不动。
- **做了什么**（四批，详见 [docs/reviews/2026-10-29-B2.2-交付与测试报告.md](docs/reviews/2026-10-29-B2.2-交付与测试报告.md)）：
  - 新增 `hermes/scripts/twin_scheduler.py`：只读决策 `plan`（原因码 + 证据 + 下一步）；调度预留（库 v3 `sched_claims`，写事务复核 + 部分唯一索引）；`tick` 只开单不开跑（`--execute` 还要 `DT_SCHED_ENABLE=1`，正式未启用）；按关联键补偿恢复 `recover`；生命体征 `observe`；取消 `cancel`（命令行只出建议）；重试建议；低噪声报告 `report --quiet`。
  - `dispatch.py`：派单根目录文件锁（编号、并发检查 + 写 running），run 记 `dispatch_pid / run_host / run_started`、车间自成进程组，`new --sched-key`；手工派单输出与字段不变。`run_worker.py`：一启动写 `out/worker.json`。`twin_state.py`：库 v3、只读打开、预留方法，迁移失败时关闭连接。
  - 文档：[D1 设计](docs/B2.2-调度与恢复机制设计.md)、[D2 协议](docs/B2.2-调度与异常协议.md)、[D6 部署准备清单](docs/B2.2-部署准备清单.md)、D3 交付与测试报告（含 D5 B2.3 交接）；tests README、AGENTS 结构。
- 测试：基线 `ecbb50a` 两套 Python 各 `Ran 188 tests` `OK`；新测试先在改前代码上跑红（dispatch 锁 9 项中 8 项红：并行开单撞号崩溃、同仓库两单同时跑、同一单跑两次）；最终 `python3 -B -m unittest discover -s hermes/scripts/tests`（3.14.5）`Ran 259 tests` `OK` 退出码 0；`/usr/bin/python3 …`（3.9.6）`Ran 259 tests` `OK` 退出码 0（原 188 + 新 71；原测试只改 2 处写死的 `schema_version == 2`）。证据 `~/Library/Application Support/DigitalTwin/evidence/b2.2-20261029/`。
- 本轮新坑：无。（事实：macOS `ps -o lstart` 的输出随语言环境变化，记录与核对进程启动时间要都用 `LC_ALL=C` 才能逐字比较。）
- 没做：未合并、未安装、未建正式库；未建 cron；未对真实项目派单、取消或重试；未改 `.env` / 技能 / SOUL / config / 网关。**自动开单与真实取消信号仍关闭**，要动作级授权（B2.x）和常浩单独批准（D6 的 B22-G6 / G7）。
- 下一步：PR 交 ChatGPT 独立复核；通过后按 D6 逐项批：B22-G1 合并 → G2 安装四个脚本（合并后到安装前 09:50 自检会报这四个文件，属预期）→ G3 手工派单回归。

---

## 2026-10-29 晚 · B2.1 合并（G1）与安装（G2）：已合并 `04b208d`、四个脚本已安装，未启用

- 时间：开工 17:20
- **来由**：ChatGPT 复跑 188/188 后出《B2.1 合并与安装收尾交接 V1.0》，B2.1 技术返修通过；常浩 17:28 说「合并并安装」，即批准 G1 合并 PR #28、G2 安装四个脚本；G3–G7 未批。
- **做了什么**：
  - 合并前复核：PR HEAD 仍是已验收的 `ecbb50a`；`origin/main...HEAD` = 0 / 8，无需变基；主目录无未推提交；PR 差异密钥扫描无命中。
  - G1：`gh pr merge 28 --squash --match-head-commit ecbb50a…` → 合并提交 `04b208d`，文件树与 `ecbb50a` 完全一致；Issue #27 自动关闭；远端分支自动删除；主目录 `pull --ff-only` 拉齐。
  - G2：旧安装副本 `twin_state.py`（sha256 `3afe51c4abd4…`）、`dispatch.py`（`f4888d49191c…`）先备份到证据目录（700 / 600）；`twin_events.py`、`twin_identity.py` 原本不存在。四个文件复制到 `~/.hermes/scripts/` 后逐个 `cmp` 与 main 一致；`/usr/bin/python3` 在安装目录可导入四个模块（schema v2）；安装版 `self_check.py` 静默、退出码 0。
- 测试：合并前在 `ecbb50a` 上 `/usr/bin/python3 -B -m unittest discover -s hermes/scripts/tests`（3.9.6）`Ran 188 tests` `OK` 退出码 0；`python3 …`（3.14.5）`Ran 188 tests` `OK` 退出码 0。证据 `~/Library/Application Support/DigitalTwin/evidence/b2.1-merge-install-20261029/`（`pre-merge-regression.txt`、`post-install-checks.txt`、`backup-before-install/`）。
- 本轮新坑：无。
- 没做（照实）：正式库 `twin-state.db` 未建（G4）；`owner_chat_id` 未配（G3），`--by user` 仍一律拒绝；`.env` 无 `DT_STATE_NOTIFY`（G5），通知钩子不动作；技能 / cron / launchd / 网关 / SOUL 未改、未重启（G6）；正式用户授权动作与自主派单关闭（G7）。旧 worktree `~/Sandbox/worktrees/digital-twin-27-b2-1` 与本地分支先保留，B2.2 不再依赖后再清理。
- 回退：`cp -p <证据目录>/backup-before-install/{twin_state.py,dispatch.py} ~/.hermes/scripts/`，删除 `~/.hermes/scripts/twin_events.py`、`twin_identity.py`；代码回退走 `git revert 04b208d` 的 PR。
- 下一步：B2.2（持久调度与跨会话恢复）在独立会话与分支上隔离开发，以 main `04b208d` 为基线；G3–G7 仍按接入清单第 2 节逐项单独批。

---

## 2026-10-29 晚 · B2.1 定向返修（ChatGPT 评审 P0-1 / P0-2 / P1-1 / P1-2 / P1-3 + P2 一条；[PR #28](https://github.com/Natsukashii1997/digital-twin/pull/28) 同一分支，待复核）

- 时间：开工 16:40
- **来由**：ChatGPT 对 PR #28 独立评审「有条件通过」（原文 [docs/reviews/2026-10-29-ChatGPT-B2.1评审原文.md](docs/reviews/2026-10-29-ChatGPT-B2.1评审原文.md)）：P0 两条身份闭环、P1 三条事务 / 重试 / 来源边界要在同一分支定向返修。按源码逐条核实属实；常浩确认三项取舍（事务化命令行；配置缺失算临时失败；bot 身份裸 API `chat_mode` 判私聊）后开工。
- **做了什么**（每条先写失败用例跑红再改，三个提交 `51b9248` → `73754ea` → `060de51`，详见 [docs/reviews/2026-10-29-B2.1-返修说明.md](docs/reviews/2026-10-29-B2.1-返修说明.md)）：
  - P0-1 `twin_identity.verify_message`：回读 message_id 必须一致、`sender_type` 必须明确 `user`、`owner_chat_id` 必配且 `chat_id` 必须相等，p2p 标志只作附加证据；`lark_fetch` 要求恰好一条、就是这条、字段齐。
  - P0-2 `setup-chat`：bot 身份 `GET /open-apis/im/v1/chats/{chat_id}` 取 `chat_mode`，只有 `p2p` 才写，群聊拒、拿不到 fail closed 给手工说明；`--write` 临时文件 + 原子替换，失败保留原文件。真实只读探针确认私聊返回 `chat_mode=p2p`（打码证据 `evidence/b2.1-rework-20261029/probe-chat.masked.json`）。
  - P1-1 `twin_state` 命令行：`done()` 改抛 `_Done`，占用与动作同一事务，只有成功才提交；崩溃 / 拒绝 / `StateError` 整体回滚。
  - P1-2 `twin_events`：临时失败不记事件可重试；永久拒绝锁住 event_id，再来返回 `rejected_before` 要新消息；命令行三种文案。
  - P1-3 `Store.trusted_order_dir`：`order_dir` 必须是派单根目录的直接子目录、非符号链接、目录名等于单号；`dispatch.py` 通知传自己的 `ROOT`。
  - P2 `normalize` 对 `identity / payload / refs` 非对象给 `EventError`。
  - 文档：协议 1.4 / 1.6 / 2.1 / 2.4 / 2.5 / 3、接入清单步骤 3、tests README。
- 测试：新用例先在 `9d5a363` 上跑红（身份 5 FAIL；再 4 FAIL + 1 ERROR；状态库 2 FAIL；事件 3 FAIL + 7 ERROR，证据 `*-red.txt`）；改后 `python3 -B -m unittest discover -s hermes/scripts/tests`（3.14.5）`Ran 188 tests` `OK` 退出码 0；`/usr/bin/python3 …`（3.9.6）`Ran 188 tests` `OK` 退出码 0（`full-regression.txt`）。原 166 项保留（1 项按评审反转）。真实环境只读（一次 bot 身份查会话信息）。
- 本轮新坑：无。（事实：飞书 `GET /im/v1/chats/{chat_id}` 用 bot 身份对私聊返回 `chat_mode=p2p`，可作私聊判定；Python `Path.resolve()` 在 macOS 把 `/var` 解析到 `/private/var`，边界比对两边都要 resolve。）
- 没做：未合并、未安装、未建正式库、未配 `owner_chat_id`、未改 `.env` / 技能 / SOUL / cron / 网关、未发真实派单。**技术基建完成 ≠ 正式可信自主授权可启用**：网关可信凭据、动作级确认仍留 B2.x。
- 下一步：推送后 Issue #27 留评、返修说明交 ChatGPT 复核；复核通过后进入 PR #28 的最终技术合并审批，合并 / 安装与 `owner_chat_id` 配置、正式启用仍按接入清单第 2 节逐步单独批。

---

## 2026-10-29 · B2.1 状态、事件与可信身份接入（[PR #28](https://github.com/Natsukashii1997/digital-twin/pull/28) 待评审，未合并、未安装）

- 时间：开工 15:00
- **来由**：常浩《Hermes B2.1 正式工程交接与施工任务书 V1.1》；分批计划经常浩确认（T01 后停一次；`--by user` 只认飞书回读核实的消息；两项真实只读探针）。Issue #27，分支 `claude/27-b2-1-events`（从 `b2c553e`），worktree `~/Sandbox/worktrees/digital-twin-27-b2-1`，主目录不动。
- **做了什么**：
  - T01：隔离库（证据目录）用真实台账只读跑 `sync-orders / recover`，两套 Python 逐单核对 15 / 15 一致、0 目标 / 任务；3 个无 order.json 的目录、3 张无存档的历史续跑、2 单 need_decision；同秒连写漏 `merged_to`。报告 [docs/reviews/2026-10-29-B2.1-真实派单台账兼容性核验报告.md](docs/reviews/2026-10-29-B2.1-真实派单台账兼容性核验报告.md)；常浩定：C1、C2 做，C3 只记。
  - T02 `hermes/scripts/twin_events.py`：统一信封 + 幂等接入，正文只留哈希，信封自带身份结论不信，拒绝留痕不消耗消息。
  - T03 `hermes/scripts/twin_identity.py`：message_id → 猛鬼兽 bot 身份回读飞书 → 本人 / 私聊（本机 `owner_chat_id`）/ 时效 → 库 v2 `identity_uses` 一条消息一次动作；`twin_state.py --by user` 改走 `--message-id`，`--sender` 退役。探针 ①（bot 回读一条常浩私聊）做了，字段形状打码存证据；网关不签发可回验凭据，缺口与最小替代写进协议。
  - T04 `dispatch.py save()` 后 `notify_state`（默认关闭、库不存在不建库、失败只记日志、stdout 不变）；`twin_state.py` 加 `sync_order / notify_order / reconcile`；C1 无 order.json 目录计 ignored，C2 幂等键改内容哈希。
  - T05 `Store.queue()` 八桶 + 命令行 `queue`。T06 [docs/B2.1-Hermes接入与配置变更清单.md](docs/B2.1-Hermes接入与配置变更清单.md)。
  - 文档：[docs/B2.1-事件与身份协议.md](docs/B2.1-事件与身份协议.md)、[docs/reviews/2026-10-29-B2.1-交付说明.md](docs/reviews/2026-10-29-B2.1-交付说明.md)（含测试记录与 B2.2 交接）；设计文档第 7 节加 B2.1 落地说明；tests README。
- 测试：新用例先在 `b2c553e` 上跑 `Ran 47 tests` `FAILED (failures=4, errors=13)`；改后 `python3 -B -m unittest discover -s hermes/scripts/tests`（3.14.5）`Ran 166 tests` `OK`；`/usr/bin/python3 …`（3.9.6）`Ran 166 tests` `OK`；原 119 项不动。真实环境只读（台账 mtime 不变；bot 回读一条私聊），证据 `~/Library/Application Support/DigitalTwin/evidence/b2.1-t01-20261029/`、`b2.1-t03-20261029/`、`b2.1-t02-t05-20261029/`。
- 本轮新坑：无。（两条事实：系统 Python 3.9 不接受嵌套同引号的 f-string；lark-cli 的 bot 身份列不出私聊会话、`--user-id` 只认用户身份、回读结果没有会话类型，所以私聊判定靠本机 `owner_chat_id`。）
- 没做：未合并、未安装、未建正式库、未配 `owner_chat_id`、未改 `.env` / 技能 / SOUL / cron / 网关、未发真实派单；探针 ②（常浩在飞书让 Hermes 只回 `HERMES_SESSION_*` 变量名）待常浩做；B2.2 项见交付说明 7.2。
- 下一步：交 ChatGPT 独立评审；通过后常浩按接入清单第 2 节逐步批：合并 → 安装四个脚本 + 自检静默 → `setup-chat --write` 配 `owner_chat_id` → 探针 ② → 正式库 `init + backup + reconcile` → `.env` 加 `DT_STATE_NOTIFY=1`。明早 09:50 自检在合并前应静默。

---

## 2026-10-29 · B0/B1 第二轮评审收口 F1～F4 + B3 审查接口（已合并 `11a69c0` 并安装）

- 时间：开工 11:30
- **来由**：ChatGPT 第二轮评审「返修基本通过」（原文 [docs/reviews/2026-10-29-ChatGPT第二轮评审原文.md](docs/reviews/2026-10-29-ChatGPT第二轮评审原文.md)），要求在 PR #26 上做最后一轮小范围收口：F1 台账冲突时提前覆盖了回执与证据、F2 续跑被拒绝时 Run 已先变 running、F3 当前 Run 按插入顺序会被后关联的旧工单夺走、F4 常浩 open_id 硬编码在源码里；另要明确 B3 独立审查任务的接口。按源码逐条核对，四项都属实。
- **做了什么**（同一分支 `claude/25-state-store`，不改派单系统）：
  - F1 `_apply_order` 先判 `_resync_kind` 再决定写什么：冲突时 `receipt / evidence / session_id` 一个字段不改，台账状态和有差异的回执 / 证据字段记进 `run.conflict` 的 payload。
  - F2 抽出 `_resume_check`（当前 Run、任务终态 / observed、阻塞类型与 by、重试上限、目标授权）；`resume_run` 先查再把 Run 置 running，不合法直接抛 `StateError` 子类、Run 不动；台账观察到的续跑保留 Run → running，任务侧记 `run.resume_conflict`，该单计入 `sync_orders` 的 `conflicts`。
  - F3 `tasks` 加 `current_run_id`（直接改 v1 schema，没有已部署的库）：`start_run` 和关联进行中的 Run 时设置，关联已结束的旧单只作证据；`current_run()` 读它，为空回退到最近开始的 Run。
  - F4 删 `OWNER_OPEN_ID` 常量；`owner_open_id()` 先读环境变量 `DT_OWNER_OPEN_ID`，再读 `~/Library/Application Support/DigitalTwin/owner_open_id`（`DT_OWNER_FILE` 可改，单行，权限不是 600 不认）；两处都没配置时 `--by user` 一律拒绝并提示怎么配。“角色声明不是身份认证”的结论不变，身份桥接仍留 B2。
  - B3 接口（最小实现）：`runs` 加 `purpose`（execute / review）；`start_run(purpose="review")` 只对 review 里的任务开只读审查 Run，审查方不能是执行方，任务状态不变、不成为当前 Run，回执只记 `run.review_result`；结论走 `accept_task(by="codex", cites=[审查 Run])`；命令行 `run-start --purpose review`。
  - 文档：设计文档 3.1 / 3.2 / 3.3 / 3.5 / 3.6 / 3.7 / 3.8 / 4 / 6 / 7 / 8.1 同步；第三轮返修说明 [docs/reviews/2026-10-29-B0B1-第三轮返修说明.md](docs/reviews/2026-10-29-B0B1-第三轮返修说明.md)（交 ChatGPT 复核）。
- 测试：新增 5 项（F1～F4、B3 各一），改 3 项断言（`test_03` 后挂旧单不改当前 Run；`test_followup_resume_rules` 到上限 / decision 阻塞的主动续跑改为拒绝；`test_cli_roundtrip` 身份改走临时配置文件并加审查 Run 一段），`test_twin_state.py` 共 31 项；先在第二轮评审对象 `2e496f1` 上跑确认 8 项不过（7 FAIL + 1 ERROR），再在收口后跑：`python3 -B -m unittest discover -s hermes/scripts/tests`（3.14.5）`Ran 119 tests` `OK`；`/usr/bin/python3 …`（3.9.6）`Ran 119 tests` `OK`。原 88 项不变。
- 本轮新坑：无。
- 没做：仍未合并、未安装、未建身份配置文件、未改技能与 Hermes 配置；Codex 审查编排留 B3，身份桥接、事件通知留 B2。
- **第三轮评审结果**（ChatGPT，同日下午，原文存 [docs/reviews/2026-10-29-ChatGPT第三轮评审原文.md](docs/reviews/2026-10-29-ChatGPT第三轮评审原文.md)）：F1～F4 源码复核通过，119 项由其在 3.9.6 与项目 Python 下独立复跑通过；第 8 节五点全部接受（F2 内部拒绝抛异常即可、B2 外部事件入口负责留痕；F4 允许环境变量覆盖但不得输出身份值；B3 的独立性判断只接受为初版）；**B0 + B1 最终技术验收通过，不再要求第四轮返修**。一条非阻断项记进 B3 接入验收（设计文档第 7 节 B3 行）：任务要求独立审查时，审查没完成前 `complete_task` 要拒绝。
- **合并与安装**（常浩 13:10 说「合并，四项都做」）：推送前检查无未推提交、分支不落后 main（变基空操作）、密钥扫描无命中；合并前复跑 119 项，3.14.5 与 3.9.6 都 OK；squash 合并为 `11a69c0`，issue #25 自动关闭，主目录 `--ff-only` 拉齐；`twin_state.py` 复制到 `~/.hermes/scripts/`（`cmp` 与 main 一致，`/usr/bin/python3` 能起）；建 `~/Library/Application Support/DigitalTwin/owner_open_id`（取 `report_data.ME`，单行，600，用安装副本回读一致，不进仓库）；`self_check.py` 安装副本与仓库源文件各跑一次都静默、退出码 0。worktree `~/Sandbox/worktrees/digital-twin-25-state-store` 和本地分支 `claude/25-state-store` 已删（核对过分支树与 `11a69c0` 无差异；远端分支随合并删除）。
- 下一步：明早 09:50 工程自检应静默（复核无漂移）。B1 收官；B2 第一步按设计文档第 7 节在隔离库上用真实台账只读跑 `sync-orders / recover`，核对冲突与 suspect 的判定——不启用正式状态库、不让 Hermes 自主派单、身份桥接做完并有测试前不用 `--by user`。

---

## 2026-10-29 · B0/B1 独立评审返修（PR #26 继续，待第二轮评审）

- 时间：开工 10:20
- **来由**：ChatGPT 对 PR #26 的独立评审「有条件通过」（B0 认可，B1 四项 P0、六项 P1 待修；暂不合并、不安装、不进 B2）。按源码逐条核对，四项 P0 都属实。
- **做了什么**（同一分支 `claude/25-state-store`，不改派单系统）：
  - R1 `block_task` 改阻塞类型过权限：非 user 只能把类型升级成 decision，decision 类只有常浩能改；解 technical 阻塞与重试共用上限。
  - R2 任务有“当前 Run”（最后登记的那个）：旧 Run 的迟到结果只改它自己，记 `run.late_result`，不动任务；任务不在 running 时结果只记 `run.result_not_applied`。
  - R3 回执缺 `exit_code` / `ended` 或 exit_code 不是整数一律算失败并写明原因；新增 `accept_task` 验收结论（必须写依据、引用本任务证据，执行方不能自验，B3 的 Codex 审查接这里），`complete_task` 只认通过的结论，有 PR 引用或成功 Run 不等于验收通过；续跑 / 返工清结论。
  - R4 `link_run` 全量校验（活动 Run 只挂 ready 且目标已授权、无其他活动 Run 的任务，终态任务不挂，项目对不上不挂）；撤销任务 / 目标改为“已请求取消”（`cancel_requested_at`，Run 仍 running，`list_runs(cancel_pending=True)` 可查），不再直接写 abandoned。
  - 工程契约：`by=user` 明确为内部可信调用约定，命令行 `--by user` 必须带 `--sender` 且等于常浩 open_id；新建目标 / 任务重复幂等键返回既有对象；台账回退、护栏降级、终态再变记 `run.conflict` 不应用，done / failed 之后的 running 按续跑处理（`resume_run`）；回执只存白名单字段；`max_retries` 可配，注明是临时保护；恢复先看台账（缺失 / 读不了 / 没这张单记 unknown，台账说在跑只列 suspect），只有没台账的 Run 才按时间判过期；备份同秒不覆盖，恢复前查完整性和版本。
  - 文档：设计文档补事实归属、角色 ≠ 身份、验收依据、迟到结果与冲突、B2 方向（事件通知为主、定时对账兜底）、B1 边界与安全接入前置条件；返修说明 `docs/reviews/2026-10-29-B0B1-返修说明.md`。
- 测试：新增 9 项、改写 17 项，`test_twin_state.py` 共 26 项；先把新用例跑在返修前的模块（`690dadb`）上确认 R1～R4 等 12 项会失败，再在返修后跑：`python3 -B -m unittest discover -s hermes/scripts/tests`（3.14）`Ran 114 tests` `OK`；`/usr/bin/python3 …`（3.9.6）`Ran 114 tests` `OK`。原 88 项不变。
- 本轮新坑：无（台账里的时间是本机 naive 字符串，不拿来当心跳，已在代码里注明）。
- 没做：仍未合并、未安装、未改技能和 Hermes 配置；真实身份映射、事件通知钩子、进程终止留 B2。
- **第二轮评审结果**（ChatGPT，同日下午，原文存 [docs/reviews/2026-10-29-ChatGPT第二轮评审原文.md](docs/reviews/2026-10-29-ChatGPT第二轮评审原文.md)）：返修基本通过，114 项由其独立复跑通过；B0 通过；要求在 PR #26 上做最后一轮小范围收口 F1～F4——① `_apply_order` 先判冲突再合并回执与证据，冲突独立留痕；② `resume_run` 先查授权与任务状态再把 Run 置 running，台账观察到的续跑保留事实并标冲突；③ `current_run` 不能只按插入顺序，历史工单后关联不得夺取当前归属；④ `OWNER_OPEN_ID` 改由本机受控配置提供，不硬编码；另要明确 B3 独立审查任务如何关联待验收 Task 的接口方案。其余六项设计接受。
- 下一步：新会话做 F1～F4 收口（同一分支 `claude/25-state-store`，worktree `~/Sandbox/worktrees/digital-twin-25-state-store`），先复现后修复，114 项 + 新用例在 3.14 与 3.9 下通过，写第三轮返修说明交 ChatGPT；通过后常浩批准“合并 + 安装副本 + 自检复核”一次收尾，再进 B2。

---

## 2026-10-29 · 七层架构 B0 复核 + B1 持久状态底座（PR 待合并）

- 时间：开工 09:10
- **来由**：常浩《Hermes Digital Twin · 七层架构基建工程启动任务书 V1.0》，本轮只做 B0 + B1，隔离分支提交、开 PR、不合并、不安装、不动运行中的 Hermes。
- **B0 复核**：任务书 9 条“已知现状”逐条核实都成立（源码与安装副本一致、88 项测试 OK、15 张真实工单、三个技能版本）；没有和七层目标冲突的正式需求；Hermes 自带 `kanban.db` 不拿来当长期状态。七层映射、对象关系、状态机、授权边界、三个事件入口、交接协议、文件清单、取舍、回退都在 [docs/B0-七层架构复核与状态底座设计.md](docs/B0-七层架构复核与状态底座设计.md)。
- **B1 做了什么**：issue #25 → [PR #26](https://github.com/Natsukashii1997/digital-twin/pull/26)，分支 `claude/25-state-store`（worktree `~/Sandbox/worktrees/digital-twin-25-state-store`）：
  - 新增 `hermes/scripts/twin_state.py`（只用标准库，兼容系统 Python 3.9）：Goal / Task / Run / Event / Ref 五张表，SQLite 在 `~/Library/Application Support/DigitalTwin/twin-state.db`（`DT_STATE_DB` 可改，700/600，WAL，版本迁移，在线备份 / 恢复）；任务状态 `observed / ready / running / blocked / review / completed / canceled` 查表转换；Run 沿用台账状态词另加 `stale`；授权、解 decision 阻塞、完成 / 撤销目标只认 `by=user`；事件幂等键 + `dup_count`，状态和事件同一事务；`recover()` 处理过期 Run、孤儿任务、依赖放行；`sync_orders()` 只读同步 `~/.hermes/dispatch` 台账为未关联 Run，不建目标；命令行每条末行 JSON，和 `dispatch.py` 一致。
  - 新增 `hermes/scripts/tests/test_twin_state.py` 17 项：任务书 B1.5 十项验收各一项（编号对应），加非法转换、撤销规则、返工心跳、备份恢复、权限、命令行往返。
  - 没改 `dispatch.py`、`run_worker.py`、技能、`ops/*`、任何安装副本和 Hermes 配置；台账写入钩子留到 B2。
- 测试：`python3 -B -m unittest discover -s hermes/scripts/tests`（3.14）`Ran 105 tests` `OK`；`/usr/bin/python3 -B -m unittest discover -s hermes/scripts/tests`（3.9.6）`Ran 105 tests` `OK`。主目录 `main` 改动前基线 88 项 OK。真实环境只读核对，没有写入。
- 本轮新坑：系统 Python 3.9 自带的 SQLite 用 `file:…?mode=ro` 打不开 WAL 库（`unable to open database file`），3.14 可以；读备份文件加 `immutable=1`。只记在这里，还没到提升为全局坑的程度。
- 没做：没安装到 `~/.hermes/scripts/`（合并后另需授权；装之前 `self_check` 每天会报安装缺文件）；没给 `dispatch.py` 加写入钩子；没改技能读状态库。
- 范围外：`hermes gateway status` 仍提示 launchd 服务定义比安装旧（旧记录已有）。
- 下一步：常浩看 PR #26；说“合并”后变基复跑 105 项、squash 合并、安装 `twin_state.py` 到 `~/.hermes/scripts/`（不用重启网关）；然后按设计文档第 7 节进 B2（先用 cron 跑 `recover`，技能改读状态库）。

---

## 2026-10-28 · 首日运行核对；巡检定时任务误报修复（已上线）

- 时间：开工 09:42
- **首日运行**：
  - 07:33 日报（新结构·并跑）首跑 ok，主模型 gpt-6.1-sol，回复「日报已写入：📅 2026-10-27（周二）（20 件开着，1 件今天关闭）」。
  - 09:45 launchd 巡检报 3 条，逐条核对都是修复前遗留：辅助任务回退 1 次、7 次「no Codex OAuth token found」都在 10-27 05:30 之前（假冷却修好前）；「定时任务有未处理的失败记录」是 10-27 中午两个已删 T7 探针留下的 incident，已 `hermes cron incidents ack`（closed）。
  - 09:50 工程自检 `f3388d53ce97` 跑完，`empty stdout — silent run`，没发消息（安装副本全部一致）。
- **发现**：`check_cron()` 跑不带参数的 `hermes cron incidents`，closed 的也算，明天还会误报；告警文字取的是表格边框行。
- **做了什么**（常浩：修，走 PR）：issue #23 → [PR #24](https://github.com/Natsukashii1997/digital-twin/pull/24)：只查 `--state detected`、`--state alerted`，按条数报并附报错摘要；自测 +2。
- 测试：`python3 -B -m unittest discover -s hermes/scripts/tests` 88 项 OK；真实环境新 `check_cron()` 返回 `[]`。
- 本轮新坑：全局 G-14（删探针定时任务要连同失败记录一起关；巡检按状态筛未处理记录），agent-kit `5c2970e`。
- **合并与安装**（常浩说“合并”）：变基复跑 88 项 OK，squash 合并为 `38a8b7e`，issue #23 关闭，worktree 和分支已删；`health_check.py` 同步到 Application Support bin（`cmp` 一致，旧版备份 `evidence/self-check-20261027/health_check.py.before-pr24`）；终端跑 self_check 无输出。
- 下一步：10-29 09:45 巡检应只剩真实问题（预计无告警），09:50 工程自检应静默。

---

## 2026-10-27 · 每月自检搬到 Hermes 无模型定时任务（已上线）

- 时间：开工 18:12
- **发现**：launchd 起的 `/usr/bin/python3` 读不了 `~/Documents`。一次性 launchd 任务跑安装版 `check_repos.py`，报「读不了名册（Operation not permitted）」；沙盒检查扫 `~/Documents/Codex` 也会抛错。11-01 的每月仓库自检和项目卡检查会失败（12:27 那次是终端手动跑的，没暴露）。一次性无模型 Hermes cron 探针（18:18，已删）能读名册、Vault 项目卡和仓库 git。
- **决定**（常浩）：每月部分搬到 Hermes 无模型定时任务，顺带加安装副本漂移检查（独立评审 #9）；走 PR，合并前问。
- **做了什么**：issue #21 → [PR #22](https://github.com/Natsukashii1997/digital-twin/pull/22)（子代理在 worktree 实现，Claude 审过）：新增 `ops/self_check.py`（每天漂移检查，每月 1 号加沙盒和仓库自检，有问题才输出）；`health_check.py` 只留每日部分。
- 测试：变基后 `python3 -B -m unittest discover -s hermes/scripts/tests` 86 项 OK；真实环境只读跑 self_check 报 3 项安装缺失（prompts 下 README、morning_brief，`~/.hermes/scripts/check_repos.py`），其余一致。
- 本轮新坑：无（TCC 那条 LESSONS 里已有：launchd 起的 python 读不了 Documents）。
- **合并与安装**（常浩说“合并”）：变基复跑 86 项 OK，squash 合并为 `1249192`，issue #21 关闭，主目录拉齐、worktree 和分支已删。安装：`health_check.py` → Application Support bin（旧版备份在 `evidence/self-check-20261027/`）；`self_check.py`、`check_repos.py` → `~/.hermes/scripts/`；补装 `hermes/prompts/README.md`、`morning_brief.md`。终端跑 self_check 无输出（全部一致）。
- **定时任务**：Hermes 无模型任务 `f3388d53ce97`「工程自检（每天 09:50，有问题才说）」，`50 9 * * *`，投递 `feishu:oc_ce2c…`（常浩私聊），首跑 10-28 09:50。
- **实测**：一次性任务（`--monthly`，已删）18:44:53 在网关里跑完，`delivered … via live adapter`；内容只有沙盒提醒（48 项超 30 天）和仓库自检两项（work-desk 多 8 个、personal-ai-workbench 多 14 个提交没推），没有漂移一节——说明 Hermes 子进程读得到 Documents、Vault 和仓库，`HERMES_HOME` 指向正确。
- 下一步：明早 09:45 launchd 巡检只报每日部分、09:50 工程自检应无输出；11-01 首次正式跑月度部分。

---

## 2026-10-27 · M4 补入 C3 与项目卡（PRD 12.3 拍板，批 7 开工）

- 时间：开工 17:20
- **决定**（常浩）：三点按推荐定——订阅流 692 篇不消化；团队类 71 篇按主题聚成综合页、默认受限；项目卡只做名册 7 个。先写 PRD，再开工批 7；批 8、9 等 10-30 例行跑完后交给 Obsidian 知识库项目会话。
- **做了什么**：PRD 写入 C3 的结论、M4 一行补批 7～9、决定 D18、拍板记录 12.3（现状数字、9 个问题的结论、验收、批次）。
- 测试：未做（只改文档）。
- 本轮新坑：无。
- 下一步：批 7 起草 7 张项目卡（写进 Vault `10 项目/`，`status: 草稿`），常浩一次过目后改为有效；之后 progress 技能改为先读项目卡、仓库自检加一项（走 PR）。
- **批 7 进展**（同日 17:30～18:10）：两个子代理分头起草 7 张卡，Vault 提交 `dd48aa7`（草稿）。常浩看过，冲突按推荐定（飞书统一猛鬼兽、CarvoMOTO 不例外；数码世界以收缩后的看板 + 资产库为准；agent-kit、自建技能库不补 CURRENT；GitHub 快速中心日报由 Hermes 发），改后转为有效，Vault 提交 `d51cf83`。接 Hermes 走 issue #19 → [PR #20](https://github.com/Natsukashii1997/digital-twin/pull/20)：名册加 `card`、progress 技能 0.3.0 先读项目卡、`check_repos.py` 查项目卡；76 项自测通过，真实名册 6 张卡全过。常浩说“合并”：变基到 `c5e4b44` 后复跑 76 项 OK，squash 合并为 `37b5456`，issue #19 关闭，主目录拉齐，临时 worktree 和分支已删。安装：progress 技能（0.3.0）同步到 `~/.hermes/skills/autonomous-ai-agents/progress/`、`check_repos.py` 同步到 `~/Library/Application Support/DigitalTwin/bin/`，`cmp` 与 main 一致，改前副本在 `evidence/M4-B7-20261027/`；重启网关，18:02:33 `✓ feishu connected`，`hermes skills list` 里 progress 为 enabled；安装位置手动跑自检 6 张卡都通过（launchd 环境下读 Vault 未验证，下次每月自检时看）。**飞书验收通过**：常浩 18:04 私聊问「今天牛马之家做了什么」，会话库 #1669～#1679 顺序为：加载 progress 技能 → 从名册取 `card` → `mcp__vault_read__read` 读 `10 项目/牛马之家.md` → 跑 `progress_data.py --project 牛马之家` → 回复（主模型 gpt-6.1-sol，42 秒），内容与取数输出一致，末尾带「项目卡：`10 项目/牛马之家.md`」，没贴卡片原文。M4 批 7 完成。
- 范围外：自检报 work-desk 本地 main 多 8 个提交、personal-ai-workbench 多 14 个没推；CarvoMOTO 仓库 D18 和 GitHub 快速中心需求文档里的飞书说法待各自仓库更新。

---

## 2026-10-27 · 按独立评审对齐三处配置（#2 #3 #12，已生效）

- 时间：开工 16:45
- **来由**：Fable 5.1 独立重梳并对比评审，报告 [docs/reviews/2026-10-27-独立重梳与对比评审.md](docs/reviews/2026-10-27-独立重梳与对比评审.md)（13 条建议）。**决定**（常浩）：先做 #2 #3 #12；飞书只认常浩，群里不听别人；报告提交进仓库。第 11 条（日报加“常浩自己写一句”）和常浩原话“了解我的一天不需要通过诉说”相反，不采纳。
- **做了什么**（只改本机安装，仓库代码没动）：
  - **#2 SOUL 日报来源**：「信息从哪来」第一条改为以 Hermes 自己写的新日报、周报两份飞书文档为准，5280 接口只作日报机器人的旧日报历史。
  - **#3 凭据借用**：`hermes config set auth.adopt_external_logins false`。Hermes 自己有 `openai-codex` 登录（device_code），不再去读、刷新 Codex CLI 和 Claude Code 的登录，避免把那边顶掉线。
  - **#3 飞书准入**：`~/.hermes/.env` 加 `FEISHU_ALLOWED_USERS=ou_5ad2…`（常浩在猛鬼兽下的 open_id，即配对表里唯一一位）和 `FEISHU_GROUP_POLICY=allowlist`。私聊只放常浩；群里只有常浩 @ 它才处理，其他人一律丢弃。`FEISHU_ALLOW_ALL_USERS` 本来就是 false。
  - **#12**：删掉 9 个已完成的一次性探针 cron，只剩日报、周报两条。
  - 重启网关一次。
- 测试：回读 `config.yaml` 为 `adopt_external_logins: false`，`hermes auth list` 提示不再借用外部登录；网关日志 16:50:50 `✓ feishu connected`；`hermes cron list --all` 只剩两条 active，`cron status` 显示网关在跑。17:06 常浩在私聊亲自发了一句：网关日志记为 `Inbound dm message`，发送人 `ou_5ad2…` 放行，主模型 `gpt-6.1-sol`（openai-codex）8 秒回复，`content_delivered=True`；重启后没有 `policy_rejected` 记录。
- 证据：`evidence/config-align-20261027/`（SOUL、.env、config.yaml、cron 列表的改前备份）。
- 范围外：`hermes gateway status` 提示「launchd 服务定义比当前安装旧，建议 `hermes gateway start`」，没处理。评审其余建议（#4 台账脚本化最急）未做。
- 本轮新坑：无。

---

## 2026-10-27 · Hermes 对话里读飞书改用猛鬼兽用户身份（已生效）

- 时间：开工 13:32
- **决定**（常浩）：Hermes 工作区改绑到猛鬼兽，复用 12:16 那次授权；亚古兽不要了；验收用 `hermes cron` 只跑一次的探针；只记 CURRENT，不开 PR（这次只改本机配置，仓库代码没动）。
- **原因**：`lark-cli` 检测到 Hermes 环境就读 `~/.lark-cli/hermes/config.json`。这份配置 8-11 绑的是旧机器人「亚古兽-Hermes」`cli_a965c429ef785bd3`，Hermes 换成猛鬼兽 `cli_a96f5f9ac63adcd4` 后没有重新绑定。亚古兽的用户刷新令牌 10-27 18:37 到期。`LARKSUITE_CLI_CONFIG_DIR` 只换配置根目录，Hermes 环境下仍会拼上 `/hermes/`，绕不开检测。
- **做了什么**（只改本机配置）：
  - 备份旧配置到证据目录，跑 `HERMES_HOME=~/.hermes lark-cli config bind --source hermes --identity user-default`：Hermes 工作区换成猛鬼兽（`replaced: true`）。
  - 绑定后用户列表为空，手动补上常浩在猛鬼兽下的用户条目 `ou_5ad2…`。令牌存储 `~/Library/Application Support/lark-cli/<appId>_<openId>.enc` 各工作区共用，所以直接复用已有授权，不用扫码。
  - SOUL 第 24 行原来的写法不用改。默认配置 `~/.lark-cli/config.json` 没动，里面亚古兽那份留着不删。T7 脚本去掉环境变量的处理保留，不影响。
- 测试：
  - 改绑前复现：`hermes chat --oneshot` 里 `--profile cli_a96f5f9ac63adcd4` 报 profile not found，不带 profile 用的是 `cli_a965…`。
  - 改绑后在 Hermes 环境下：`auth status` user 身份 `valid`，`authen/v1/user_info --as user` 返回常浩 `ou_5ad2…`；默认配置下猛鬼兽仍 `valid`。
  - 网关探针 `92287fb372f0`（带模型、只跑一次、只存本地，已自动清掉）：按 SOUL 写法跑 `lark-cli`，appId `cli_a96f5f9ac63adcd4`、user 身份 `valid`、私聊读取成功（读到 1 条 13:32 的文本消息，证据只记 ID 和时间、不记正文）。
  - 证据在 `evidence/lark-profile-20261027/`（复现输出、旧配置备份、探针汇总）。
- 本轮新坑：Hermes（OpenClaw、lark-channel 同理）换飞书应用后，要重新跑 `lark-cli config bind`，否则对话里的 `lark-cli` 还在用旧应用；`config bind` 是一次性同步，不会自动跟着变。并进上一节那条，一起提议提升为全局坑，等常浩批。
  - 常浩 14:23 在飞书私聊里亲自问猛鬼兽读一段昨天的私聊：会话库里两次业务调用都带 `--profile cli_a96f5f9ac63adcd4 --as user`（`contact +search-user`、`im +chat-messages-list`），都 `ok: true`、`exit_code: 0`，回复 493 字。
- 没做：没开 issue / PR（常浩定的）。
- 下一步：不变，见下一节（明早 07:30 日报首跑后回读）。

## 2026-10-27 · M5 T7 批 2：Hermes 按新结构写日报、周报（定时任务已建，明早首跑）

- 时间：开工 12:20
- **决定**（常浩）：台账和词表都按建议定。台账种子在 `~/Library/Application Support/DigitalTwin/ledger-seed.md`（18 件，内部内容不进仓库）；词表 `hermes/prompts/project_vocab.md` 定稿。
- **做了什么**（PR #15，常浩 10-27 说「合并」，已 squash 合并 `f995061`；变基时测试说明 README 和 main 新增的两节冲突，两边都保留；合并前全套 `Ran 75 tests` `OK`；安装副本和 main 逐个核对一致；分支和 worktree 已删）：
  - `hermes/scripts/report_data.py`：日报取数。读并跑文档里前一篇的「二」「三」，没有就读种子。消息只收私聊（含自己给自己的备忘，不含和猛鬼兽的私聊），群只收常浩当天发言或被 @ 的。另取会议、妙记、日程，飞书任务只收常浩负责、近期到期或当天新建的。再加上项目进展、词表和覆盖统计。同一天已写过、周末没动静都输出 SKIP。周报读本周的新日报，加上一篇周报的「六」。
  - `report_publish.py`：检查标题、星期、六段、覆盖行，不许有表格、不许重复。插到文档说明段之后，写后回读，另存一份到证据目录 `evidence/reports/`。失败不重试。项目名粗体行前后自动补空行。
  - `hermes/prompts/daily_report.md`、`weekly_report.md`；定时任务入口 `report_daily.py`、`report_weekly.py`。
  - 安装：脚本放 `~/.hermes/scripts/`，提示词和词表放 `~/.hermes/prompts/`（新目录），已逐个核对和源文件一致。
  - 定时任务：「日报（新结构·并跑）」`33df73dffd17` 每天 07:30；「周报（新结构·并跑）」`6a976baa2632` 周一 07:45。结果只写进文档（deliver local），失败通知常浩私聊。首跑分别是 10-28 07:30（写 10-27）和 11-02 07:45。
- 测试：
  - `python3 -B -m unittest discover -s hermes/scripts/tests`：`Ran 56 tests` `OK`，其中新增 test_report 13 项。
  - 让 Hermes 用 10-27 截至中午的真实材料试写两次（`hermes chat --oneshot`，只写文件不发布）。第二次 3320 字，台账 19 件，`check` 通过。第一次暴露三个问题，已改提示词和取数：个人项目被建成行动；只有链接、截止在 2027 年的飞书任务被建成事项；知识库不在材料里也硬写一行。
  - 网关探针（`--no-agent`、只跑一次的定时任务，共 5 个，已自动清掉）：
    - 探针 1、2：取数失败。
    - 探针 3：读环境变量名。
    - 探针 4：修好后取数通过，项目进展也读到了。
    - 探针 5：写回一份临时测试文档，回读格式正确。
  - 证据在 `evidence/T7-20261027/`（试写、材料、探针输出）。
- **发现**：`lark-cli` 看到 `HERMES_HOME`、`HERMES_QUIET`、`HERMES_EXEC_ASK` 等变量（OpenClaw、lark-channel 同理），会改读 `~/.lark-cli/hermes/` 那套配置。那套配置里只有另一个应用 `cli_a965…`，没有猛鬼兽，所以网关里 `--profile cli_a96f5f9ac63adcd4` 报 profile not found。T7 的脚本在调用时去掉了这些变量。**范围外**：SOUL 里写的「Hermes 用猛鬼兽上常浩的用户身份读飞书」，在 Hermes 自己的对话和工具里同样不生效，F2.2 终局之前要另外处理。
- 本轮新坑：上面这条 `lark-cli` 换配置目录（项目还没有 LESSONS，先记在这里；提议提升为全局坑，等常浩批）。
- 没做：
  - 「T7 写回测试（可删）」这份临时文档还在常浩「我的空间」根目录。`drive +delete` 要 `--yes` 强制确认，没删，常浩可以手动删。
  - 没验证模型在网关定时任务里完整跑一遍（明早 07:30 首跑就是第一次）。
- 下一步：明早 07:30 首跑后回读并跑文档里的 10-27 日报，请常浩看；之后进批 3（`brief_data.py` 和晨报提示词改读新结构，出样稿）。

## 2026-10-27 · M3 批 6（B 总验收）：M3 完成

- 时间：开工 11:55
- **决定**（常浩）：分批计划照做，3 个问题按推荐定：交替接手用 digital-twin；其余 4 个项目不各跑真实单，用自检证明派单前提、例外写明；github-center 主目录切到 main。
- **只读核对**：5 个远端都是私有，默认 main，只允许 squash，合并后自动删分支，Issues 打开，5 个分流标签齐全；远端 main 上都有 PR 模板和《工程流程》指针；名册的 `github`、`main` 和实际一致。
- **github-center**：主目录从 `claude/v1` 切到 `main`，快进 1 个提交，`claude/v1` 没有独有提交，保留不删；那个没跟踪的文件 `design/candidates/pages/index.html` 原样留着。launchd 里没有任务指向这个仓库。名册说明的改动走 PR #18（issue #17），已合并 `15b91af`。
- **交替接手**（都用 `dispatch.py` 直接派，就是 Hermes 用的那个脚本，没经飞书；飞书全流程批 5 的 D20261027-1 已走通）：
  - D20261027-2 · Claude：开单建 issue #11 → 车间 3 分钟做完 → PR #13 → 常浩说“合并” → squash `9c5dd53` → 清理。新增只读自检 `ops/check_repos.py`。车间自己拿了 3 处主意（按名册 `agents` 字段查指针、本地落后只算提示、补正测试总数），都写在 PR 里。
  - D20261027-3 · Codex：任务说明只写了“接着 issue #11 / PR #13 做”，背景没写。Codex 自己从 issue、PR 和仓库里读懂了自检的输出格式，以及失败和提示的区别，接进每日巡检：每月 1 号跑，`--repos` 可手动触发，只把失败项按月提醒一次。开单建 issue #14 → 车间 5 分钟 → PR #16 → squash `29a833b` → 清理。
  - **两次都只靠仓库内容接上，B 验收通过。** 唯一要人工处理的一处：Codex 按 AGENTS 在 CURRENT 顶部写交接，T7 同时也在 CURRENT 顶部提交了，变基冲突，Claude 两节都保留后再开 PR。
- **安装**：`health_check.py`、`check_repos.py` 同步到 `~/Library/Application Support/DigitalTwin/bin/`（launchd 调用的就是这里）。同步前确认安装副本和改前的仓库版本一致，旧副本在 `evidence/M3-B6-20261027/`。
- **各项目的派单全流程**（开单、建 issue、跑单、开 PR、squash 合并、清理）：

  | 项目 | 结论 |
  |---|---|
  | digital-twin | 实跑 3 单通过（D20261027-1、-2、-3） |
  | github-center | 前提全过（主目录已切 main） |
  | agent-kit | 前提全过；例外：名册没写测试命令，合并时如实报 `tested: false` |
  | 牛马之家 work-desk | 设置全过；例外：本地 main 比远端多 3 个提交没推，`merge` 会先把提交列给常浩，回“推”后带 `--push-main` 才合 |
  | 数码世界 personal-ai-workbench | 同上，本地 main 多 2 个提交没推 |

  开单和开 PR 都从 `origin/main` 拉分支，本地 main 没推的提交不会混进 PR。
- 测试：自测 `python3 -B -m unittest discover -s hermes/scripts/tests` 62 项 OK（合并 #13、#16 时脚本在变基后各跑一遍，`tested: true`）；真实名册跑 `ops/check_repos.py`，只有上表两条本地没推，提示 1 条（agent-kit 没写测试命令）；安装目录跑 `/usr/bin/python3 -B health_check.py --dry-run --repos`，仓库提醒列出这两条，没有发消息。
- 本轮新坑：无。CURRENT 冲突的问题记在遗留 3。
- 遗留：
  1. GitHub 上 `digital-twin-old-to-delete`、`digital-twin-old-to-delete-2` 仍待常浩在网页上删。
  2. 代码直推 main 的情况还在发生，和 M3-Q9 不一致：digital-twin 的 `d4701f3`（巡检加辅助任务回退检查）已经推上去了；牛马之家本地 main 上的 `ca6464b`（培养路径）和数码世界本地 main 上的 2 个提交还没推。都由各自的会话处理，本轮没动。
  3. 车间按 AGENTS 在 CURRENT 顶部写交接，别的会话同时写 CURRENT 时，`merge` 变基会停下。B6 的约定是交接写进 PR 或 issue：要不要让派单的车间只写 PR、不写 CURRENT，待常浩定。
  4. 数码世界 `reverse-ai-process.test.mjs` 偶发失败，已开 personal-ai-workbench#6。
  5. ~~PR #18（名册说明）等合并~~：已 squash 合并为 `15b91af`，issue #17 已关闭。
- 下一步：M3 完成。之后按里程碑做 M4、M5（T7 进行中）；下个月 1 号巡检会第一次跑仓库自检。

## 2026-10-27 · D20261027-3：仓库自检接入每月巡检（已合并 `29a833b`、已安装，见上一节）

- 承接 issue #11 / PR #13，改动在 `hermes/D20261027-3`：每月 1 号与沙盒检查一起运行 `check_repos.py`；其他日期用 `--repos` 手动触发，与 `--sandbox` 独立。
- 自检只读取末行 JSON 的 `failed`，通过项和 `warnings` 不发；有失败时复用巡检的 `send()` 单独提醒，幂等键 `repos-YYYY-MM`。`--dry-run` 打印但不发消息。启动失败、超时或结果无效均报“仓库自检未完成”。
- 改动：`ops/health_check.py`、`hermes/scripts/tests/test_health_check.py`、测试 README、AGENTS 的巡检说明及本记录；未改自检规则、名册或其他仓库。
- 验证：新增 14 项隔离测试；`python3 -B -m unittest discover -s hermes/scripts/tests` 共 62 项 OK；`/usr/bin/python3 -B -m unittest discover -s hermes/scripts/tests -p test_health_check.py` 共 20 项 OK；`git diff --check` 通过；独立静态审查无阻塞项。测试未联网、未碰真实仓库或发消息。
- 安装副本**未同步**（本单明确排除仓库外操作）；未验证 launchd 实际运行或真实消息投递。巡检通过同目录路径调用自检，安装时须将两份脚本一起放到 `~/Library/Application Support/DigitalTwin/bin/`。
- 下一步：Hermes 按车间流程接回本分支；获准合并后由后续执行方同步两份安装副本，并验证实际运行。

## 2026-10-27 · M5 T7 批 1：并跑文档、台账初稿、项目词表（等常浩确认）

- 时间：开工 11:55
- **决定**（常浩）：T7 分 5 批（issue #12），3 个问题按推荐定：① 新文档只有常浩本人可见，日报、周报各一份；② 晨报直接用 `lark-cli` 读新文档，牛马之家来源不动；③ 周一晨报并进 9:30 周启，周五 16:00 收口照发，当天截止单推放 14:00。
- **做了什么**：
  - 猛鬼兽应用上常浩的用户授权 07-10 就过期了（刷新令牌 7 天没用就失效），常浩 12:16 重新授权，权限范围和原来一样（157 项），现在有效到 11-03，每次调用会自动续期。
  - 飞书新建两份并跑文档，放在常浩「我的空间」根目录（原周报也在这里；原日报不在根目录，位置没查到）：日报 `https://vm94j8bzy7.feishu.cn/docx/WZyhdQdM0oMk2jxSlOTcJPVKnQg`，周报 `https://vm94j8bzy7.feishu.cn/docx/QYEhdzNgUof8mtxW0cqcYErgnDc`。没加协作者。
  - 台账初稿（含原文，只在证据目录 `T7-20261027/02-台账初稿.md`）：10-14～10-26 日报第四段没写完成的 48 件，建议迁入 15 件、关闭 9 件（附依据）、不迁 23 件、按 Q3 不进日报 1 件；加上 10-23 新增 2 件和周报挂起 2 件，迁入共 18 件。10-26 的「每格一行」按 6 格一组还原（`scripts/extract_open.py`）。
  - 项目词表初稿 `hermes/prompts/project_vocab.md`（PR #15，草稿）：114 种「T线」写法归成 15 条工作线 + 6 个个人项目。
- 测试：两份文档用 `docs +fetch` 回读，标题和首段正确。分享设置没回读：读权限设置要 `docs:permission.setting:read` / `docs:permission.member:retrieve`，这两项不在授权范围里，没为此扩权限。
- 本轮新坑：无（授权过期属于一次性状况；批 4 在巡检里加「用户授权是否有效」）。
- 下一步：常浩过目台账初稿和词表（只说要改的）→ 词表 PR 合并 → 批 2：Hermes 写日报和周报的提示词、取数脚本、07:30 / 周一 07:45 定时任务。

## 2026-10-27 · 每日巡检补上辅助任务回退检查

- 时间：开工 11:50
- **决定**（常浩）：按改法做：会话库查“走了回退”，日志查“直接失败”。
- **改了什么**（`ops/health_check.py`，launchd 任务不变）：
  - `check_fallback()` 按任务分组：主对话那句告警原样不动；辅助任务另报一句“昨天辅助任务走了回退模型 deepseek-flash：title_generation N 次…”，同样排除 oneshot。
  - 新增 `check_aux_errors()`：读 `~/.hermes/logs/agent.log` 里昨天日期开头的行，数 “Failed to generate context summary”（压缩失败，这类不进会话库）和 “no Codex OAuth token found”（取不到 Codex 凭证）。这两句是 Hermes 源码里写死的日志原文，Hermes 升级改了措辞这项会失效，注释里已写明。
  - 两个检查都加了可选参数 `day`（默认昨天），方便测试和回放。
  - 安装副本 `~/Library/Application Support/DigitalTwin/bin/health_check.py`（launchd 实际跑的这份）覆盖前和改前的仓库版本一致，旧副本备份在 `evidence/hermes-aux-codex-20261027/health_check.py.before`；同步后用 `/usr/bin/python3` 回放 10-27，结果同上。
- 测试：`python3 -B -m unittest discover -s hermes/scripts/tests` 43 项 OK（原有 37 项 + 新增 `test_health_check.py` 6 项，临时目录造假库和假日志）；变异检查 2 处（去掉 oneshot 排除、不按日期过滤日志）都被抓到。真实数据只读回放：10-26 报 4 条（主对话回退 8 次、title_generation 回退 2 次、压缩失败 8 次、取不到凭证 69 次）；10-27 报 2 条（title_generation 回退 1 次、取不到凭证 7 次），都在 11:05 修复之前，修复后两类日志都是 0 条。
- 本轮新坑：无。
- 注意：明天（10-28）9:45 的巡检查的是 10-27 全天，**会报上面 10-27 那 2 条辅助任务告警**，这是修复前留下的，不是复发；主对话回退那句不该出现。10-29 那次应完全干净。
- 下一步：10-28、10-29 两次巡检结果确认；主线回到 M3 批 6。

## 2026-10-27 · Hermes 辅助任务改回 Codex（修 M2 批 E 遗留②）

- 时间：开工 11:00
- **根因**：10-26 16:57 那次回退测试用了不存在的模型 `gpt-nonexistent-test`，Hermes 在 `auth.json` 的 Codex 凭证 `openai-codex-oauth-1` 下记了一条到 2027-10-26 的模型冷却。主对话取凭证时带模型名，不受影响；辅助任务（标题生成、压缩）不带模型名，按代码规则任何一条没过期的冷却都会挡住整条凭证（`agent/credential_pool_model_cooldowns.py` 的 `model_cooldown_until`），于是报 “no Codex OAuth token found”。标题生成回退到 deepseek-flash，压缩直接失败（这就是遗留②）。首条报错 16:57:12，冷却写入 16:57:00；令牌本身有效到 11-05。
- **另一件事**：10-27 巡检报的 8 次主对话回退，是 10-26 20:00～20:02 Codex 连续 `ConnectTimeout` 后正常回退，不是这个 bug。
- **修法**（常浩确认，未改 hermes-agent 源码，未改 `config.yaml`）：先备份 `auth.json`、`config.yaml` 到 `evidence/hermes-aux-codex-20261027/`；`hermes auth reset openai-codex` 清掉冷却（令牌指纹改前改后都是 `428fa523…`）；11:05:50 重启 `ai.hermes.gateway`。
- 测试：
  - 本机 `hermes -z`：会话 `20261027_110617_6a5b39` 的主对话和 title_generation 都记在 openai-codex / gpt-6.1-sol。
  - 飞书（常浩自己发）：「嗨bro」走 openai-codex；`/compress` 日志 “Auxiliary compression: using openai-codex (gpt-6-luna)”，50 → 48 条，committed。手动压缩不写 `session_model_usage`，以日志 telemetry 为准（`aux_provider=openai-codex`）。
  - 回退：`hermes -z` 时让 chatgpt.com 走不通的代理 127.0.0.1:9、DeepSeek 直连，会话 `20261027_113100_eebd7e` 回退到 deepseek-flash；测完 Codex 凭证没有新冷却，紧接着 `20261027_113115_8b1c34` 又全走 openai-codex。
  - 11:05:50 之后 `agent.log` 里 “no Codex OAuth token found”、“main provider unavailable” 各 0 条。巡检排除 oneshot，回退测试不会让明天 9:45 报警。
- 本轮新坑：回退测试别用假模型名。Hermes 会把它记成凭证的模型冷却（一年），辅助任务不带模型名、会被整条挡住。测回退改用“只让 Codex 连不上”（单条命令设不通的代理、DeepSeek 直连），测完回读 `auth.json` 的 `model_cooldowns`。提升：常浩批准，已提升为全局 G-13（2026-10-27，agent-kit `6d78a63`）。
- 遗留：
  1. ~~巡检只查主对话回退，辅助任务回退查不到~~：已补上（见上一节）。
  2. 能不能“第二天不再报”还取决于 Codex 当天有没有网络超时；超时回退是正常行为。
- 下一步：明天 9:45 巡检结果确认没有回退告警；之后回到 M3 批 6。

## 2026-10-27 · M3 批 5（B6 交替规矩 + Hermes 接 GitHub）完成

- **决定**（常浩）：分批计划照做。另外补了一条：写入单从 `origin/main` 拉分支；合并时本地 main 有没推的提交，先把标题列给常浩，他回“推”才推（`merge --push-main <提交号>`）。批 5 的 6 个 PR 一次说“合并”全合；数码世界本地 main 上没推的 2 个提交（`1d25104`、`a38bc37`）同意推。
- **6 个 PR**（每个先建 issue，分支都从 `origin/main` 拉，只含本 PR 的提交）：
  - agent-kit#4 → `5868ae8`：新建 `工程流程.md`；README「分层」加指针；本仓库和 project-starter 骨架加 PR 模板、AGENTS 指针。
  - work-desk#4 → `ce7a2c5`、personal-ai-workbench#5 → `b352c0f`、github-center#20 → `5a48892`、digital-twin#4 → `0ec3ea4`：加 `.github/pull_request_template.md`，AGENTS「Agent skills」末尾加《工程流程》指针。
  - digital-twin#6 → `454171b`：
    - `dispatch.py`：写入单开单建 issue；新增 `pr`；`merge` 走推送前检查 → 变基 → 测试 → `gh pr merge --squash` → 主目录拉齐；`clean` 确认 MERGED 后 `branch -D`，放弃时关 PR 和 issue。
    - `run_worker.py`：远端护栏只看本单分支，其他远端变化记进 `note_remote_moved`。
    - `progress_data.py`：加当天的 PR 和 issue，GitHub 取不到只标在该项目下。
    - 技能：workshop 升 0.5.0，progress 升 0.2.0。progress 不在原计划里，但它写着“没读 GitHub”，F6.3 要用，所以一起改了。
  - 6 张 issue 都已自动关闭；worktree、本地分支和远端分支都已清理。主目录都已拉齐：github-center 用 `fetch origin main:main`，仍检出 `claude/v1`。各主目录里别人没提交的改动没动。
- **安装与网关**：三个脚本直接用 main 上的版本覆盖（覆盖前和改前的仓库版本一致）。workshop、progress 两个安装副本已被 Hermes 自己加过规矩，和仓库版本不同，所以用三方合并把批 5 的改动叠上去，保留 Hermes 自己加的内容，没有直接覆盖。旧副本和装上的版本在 `evidence/M3-B5-20261027/`。05:24 重启 `ai.hermes.gateway`，飞书重连正常。
- **飞书验收**（常浩授权，Claude 用 `lark-cli --profile codex --as user` 代发，每条都先给常浩看过）：
  - 派单 D20261027-1（Claude · 写入 · digital-twin：名册给 digital-twin 填 `test`，tests/README 补一句）。开单时自动建了 issue #7（`ready-for-agent`）。核对待确认派单和原单一致后回了“可以”；车间用时 53 秒，自动开了 PR #8（`Closes #7`）。回报里有 PR 链接，回执两条护栏都是 false，只提交了指定的两个文件。车间在 README 那句后面补了命令，并在 PR 里说明了，常浩看过 PR 后同意。
  - 常浩说“合并”：PR #8 squash 合并为 `c44180d`，主目录已拉齐，issue #7 已关闭，单子已 cleaned。合并时 `tested: false`（名册里的测试命令正是这一单才加上的），Hermes 照实说明，合并前后各自跑了一遍，都是 37 项全过。
  - 问“今天各项目怎么样”：5 个项目的回答里都有当天 PR 和 issue 的数目，digital-twin 是 PR #2、#4、#6、#8 和 issue #1、#3、#5、#7，和 GitHub 一致。**F6.3 通过。**
- 测试：digital-twin 自测 `python3 -B -m unittest discover -s hermes/scripts/tests` 共 37 项 OK（原有 20 项 + 新增 17 项：假 `gh` + 临时裸仓库；变异检查 3 处都被抓到）。work-desk `npm test` 739/739；github-center 140/140；agent-kit 骨架自测改前改后都是 4 项过 3 项（没过的是 CURRENT 占位）；数码世界 1551 项过 1549，没过的 2 项是缺 `dist/` 和 `wiki-local` 的已知环境问题。变基后整套重跑时，`reverse-ai-process.test.mjs` 的“忽略 TERM 的孙进程”失败一次，留下子进程让整套测试卡住；单独重跑这个文件 11/11 通过，判断是并发负载下偶发失败。
- 本轮新坑：无。
- 遗留：
  1. GitHub 上两个旧仓库仍待常浩删除。
  2. `merge` 仍要求主目录检出主分支：github-center 主目录在 `claude/v1`，Hermes 合并这个项目的单会停下报告。
  3. ~~仓库里的 workshop / progress 技能落后于安装副本~~：已回写（digital-twin#10 → `6cb05c9`，workshop 升 0.5.1，已同步回安装位置，旧副本在 `evidence/M3-B5-20261027/before-0.5.1/`）。
  4. 数码世界 `reverse-ai-process.test.mjs` 在负载下偶发失败、会卡住整套测试，记给数码世界处理。
- 下一步：M3 批 6（总验收）。

## 2026-10-27 · M3 批 5 计划定稿（未开工）

- **决定**（常浩，5 条都按推荐定）：
  - B5-1：写入单开单时自动建 issue，只读单不建。
  - B5-2：车间跑完就开 PR，回报里带链接。
  - B5-3：推送前检查只由脚本自动查密钥和 Token，命中就停；受限内容靠规矩防（《工程流程》第 7 节和各仓库 AGENTS 指针），不另写关键词黑名单。
  - B5-4：本地和远端分叉时停下来报告，不自动变基主目录的 main。
  - B5-5：5 个仓库各开一个 PR，常浩说一次“合并”全合。
- 草稿全文（《工程流程》、PR 模板、dispatch.py / run_worker.py / progress_data.py / workshop 的改法）在会话共享笔记 `M3-批5-草稿.md`，路径见交接卡。
- 常浩要求批 5 换到新会话做（本会话上下文过长）。
- 未改任何仓库。
- 下一步：新会话按草稿分批做批 5，开工前先给常浩看分批计划。

## 2026-10-27 · M3 批 4 合并

- **决定**（常浩）：合并批 4 的 4 个 PR，顺序是 先推本地 main → 变基 → squash 合并 → 主目录 pull。
- **推本地 main**：合并前查了 4 个仓库本地 main 比远端多出的提交。只有数码世界多 2 个（`dac9073` 便签样式、`c27af8f` 发布记录，内部级），已推送。
- **合并**：PR 分支都变基到最新的远端 main 后再 squash 合并，关联的 issue 都已自动关闭。
  - work-desk#2 → `0702fb6`
  - personal-ai-workbench#3 → `aa4f4dc`（只含本次的 5 个文件）
  - agent-kit#2 → `d4617f2`
  - digital-twin#2 → `a7b5627`
- **主目录**：4 个主目录都 `git pull --ff-only` 到了远端 main。agent-kit 先把没跟踪的 `skills/` 挪到 `evidence/M3-B4-20261027/agent-kit-untracked/`，拉下来后核对：只多了本次的改动。`~/.agents/skills/project-starter` 已经是两层版本。未提交文件数和合并前一样：牛马之家 4 个，数码世界 65 个（另有别人新建的一个未跟踪交接文件），agent-kit 只剩 LESSONS.md 的 G-12。
- **清理**：4 个 worktree 和本地分支已删除，远端分支合并时已自动删除，`~/Sandbox/worktrees` 已清空。
- 测试：合并前，digital-twin 变基后自测 20 项通过；其余 3 个仓库变基没有带进新内容，沿用开 PR 时的测试结果（见上一节）。合并后 4 个远端 main 和本地 main 一致。
- 本轮新坑：无。
- 遗留：① GitHub 上两个旧仓库待常浩删除。② agent-kit 证据目录里那份旧 `skills/` 可以删，留着备查。
- 下一步：批 5（B6 交替规矩 + Hermes 接 GitHub）：《工程流程》文档和 PR 模板；`dispatch.py` 写入单改成推分支、开 PR，常浩说“合并”时按本轮顺序执行，推本地 main 前先查 `origin/main..main` 里有没有受限内容；`progress_data.py` 加 PR 和 issue（F6.3）。

## 2026-10-27 · M3 批 4（B4 + B5）：开 PR，重建 digital-twin 远端

- **决定**（常浩）：批 4 按草稿做，开 issue、在 worktree 里写、开 PR，合并前等他说。发现受限内容被推上 GitHub 后，又定了：删掉 GitHub 上的 digital-twin 重建，M4 那 3 个提交改成只写指针和结论；71fe99e、5dc6df7 里引用个人上下文页内容的句子也一起改成页名指针。
- **PR**（都在 `~/Sandbox/worktrees/m3-b4-*`，主目录没动）：
  - 牛马之家 work-desk#2：`npm test` 739/739 通过。
  - 数码世界 personal-ai-workbench#3：1551 项中 2 项失败，都是 worktree 环境造成的（缺 `dist/`，找不到上级目录的 `wiki-local`）；补上构建、链上 wiki-local 后，这两个测试文件 19/19 通过。
  - agent-kit#2：project-starter 原样收进仓库，再改成两层一起铺；临时目录试铺通过，骨架自带测试 4/4、规则检查通过。
  - digital-twin#2：自测 20 项通过。
  - 每个 PR 关联一张 issue，都打了 `ready-for-agent`。内容见共享笔记 `M3-批4-待写内容.md`。
- **Codex 实测**：0.160.1 会读 `~/.agents/skills`；to-spec 这类只在点名时加载，点名 `$to-spec` 后能读到正文。
- **受限内容外泄与重建**：digital-twin 的 PR 分支是从本地 main 拉的，把 M4 那 3 个没推的提交（含常浩的个人决定）带上了 GitHub。处理经过：
  1. 把旧远端改名为 `digital-twin-old-to-delete`。
  2. 新建了空仓库，但 T1 刚好在这时推送，新仓库也带上了旧提交，于是又改名为 `digital-twin-old-to-delete-2`。
  3. 请 T1 暂停推送；M4 改写了自己的提交，Claude 又补改了 vault 0.1.0 里两处引用页面内容的句子。
  4. 核对全部历史，按关键词扫，命中 0。最终文件树和 M4 改写后的版本一字不差。
  5. 第三次新建 `Natsukashii1997/digital-twin`，推 main `ff800e3`；设置和 5 个标签已回读，issue #1 和 PR #2 已重开。
  - 改写前的本地历史打成 bundle 存在 `evidence/M3-B4-20261027/`（权限 600）。
- 测试：见上面各 PR；digital-twin 重建后本地 main 自测 20 项通过；新远端 main 和本地一致。
- 本轮新坑：无（教训记在下面的遗留里，批 5 写进合并流程）。
- 遗留：
  1. GitHub 上两个旧仓库 `digital-twin-old-to-delete`、`digital-twin-old-to-delete-2` 要常浩在网页上删除，本机 `gh` 没有 `delete_repo` 权限。
  2. 4 个 PR 等常浩说“合并”。
  3. 教训：从本地 main 拉分支推送，会把别人没推的提交一起带上去。批 5 的合并流程要先检查 `origin/main..main` 里有没有受限内容，再推。
- 下一步：常浩说“合并”后按 先推本地 main → 变基 → squash 合并 → 主目录 `git pull --ff-only` 合并 4 个 PR；之后进批 5。

## 2026-10-27 · M5 T1：日报和周报结构重设计（已拍板）

- 时间：开工 00:02
- **决定**（常浩）：选方案 A 台账式，11 个拍板问题全按推荐定，结构和样稿确认。结论记在 PRD 12.2，方案全文 `docs/T1-日报周报结构方案.md`。
- **做了什么**：只读拉取牛马之家日报接口一次（157 篇：日报 134、周报 23），逐段统计现行格式的 62 篇日报；用 10-23 真实日报改写成新结构，并推演了 10/26 的晨报。分析、样稿、脚本和接口快照在 `evidence/T1-20261027/`，里面有原文，不进仓库。
- **关键数字**：未完成的行动 373 条，其中 73% 之后再没出现，后来被关掉的只有 2 条；截止写成绝对日期的只有 28%；沟通记录占全文 42%。样稿从 6930 字缩到 2939 字（已含 16 件台账）。
- 测试：未改代码，无测试。样稿字数用 `len()` 实测；用 `brief_data.py` 的 `rows()` 回读了 10-21、10-23、10-26 三篇（第四段分别读出 8、14、0 行）。
- 本轮新坑：无（下面的表格转换问题属于牛马之家，不在本项目）。
- 发现：10-26 起，飞书表格经牛马之家转换后变成「空表格 + 每格一行」，`brief_data.py` 读 10-26 第四段读出 0 行。新结构不用表格，可以绕开；旧日报还要读的话，要在牛马之家另外处理。
- 没做：Hermes、牛马之家、定时任务都没动。
- 下一步：T7 按 12.2 实现。① 新建并跑用的日报文档；② 初始化台账（10-14 以后还开着的行动 + 周报长期挂起项，常浩过一遍）；③ 拟项目词表请常浩确认；④ 改 `brief_data.py` 和晨报提示词，恢复 M1 第 4 批；⑤ 并跑 1 周。开工前先给分批计划。

## 2026-10-27 · vault 技能 0.3.0：方法论类问题也先查

- 时间：开工 00:02
- **决定**（常浩）：现在修，方法论、“怎么做”类问题也先查 Vault 卡片，改完重启网关，再用“标准调研步骤是什么”自测。
- **改动**：`hermes/skills/vault/SKILL.md` 升 0.3.0，description 改为“常浩的判断类和方法论问题：先查 Vault，引用笔记作答”；触发范围分两类（关于常浩本人 / 方法论、“怎么做”，列了卡片覆盖的主题），Vault 没有相关卡片时再按通用知识答并说明；失败信号同步。已同步安装位置。`~/.hermes/SOUL.md` Vault 那句补上方法论类问题（改前副本 `evidence/M4-B3-20261026/SOUL.md.before-vault-0.3`）。网关重启，飞书 00:05 重连。
- 测试：本机 `hermes -z "标准调研步骤是什么"`（`evidence/M4-B3-20261026/selftest-vault-0.3.md`）：先读 vault 技能，vault-read 搜“调研”“调研步骤”“调研先”，读了 5 张有效卡片和 1 篇主题页，按卡片整理成“定义 → 还原 → 验证 → 诊断”四步，末尾列出 4 张卡片路径。10-26 23:57 同一问题没查 Vault，这次查了。
- 本轮新坑：无。
- 下一步：同上一节（F3 正式验收时间由常浩定；批 2 等 10-30）。

## 2026-10-27 · M4 批 5：F3 试问（未完成正式验收）

- 常浩 10-26 23:57～10-27 00:00 在飞书私聊问了 2 题，评价暂时没问题。
- Claude 回查（会话库消息 #1456～#1474）：
  - “项目收敛的原则是什么”：读了 vault 技能，用 vault-read 搜“收敛”“收敛原则”，读了一张卡片和一篇 GET 笔记，按笔记作答、给了出处。合格。
  - “标准调研步骤是什么”：**没查 Vault**，走了通用的 grounded-citations 技能，按常识作答。内容和 Vault 里调研主题的卡片方向一致，但 Vault 里有 4 张直接相关的有效卡片，没用上、没引用。原因：vault 技能的触发写的是“关于常浩本人的判断类问题”，“怎么做”的方法论问题它当成了通用知识。
- F3 正式验收（5 题）暂缓，时间由常浩定。
- 本轮新坑：无。
- 下一步：见下面 M4 批 4 一节；另有 vault 技能触发范围的小修（方法论类问题也先查卡片），等常浩定要不要现在做。

## 2026-10-26 · M4 批 4：补厚“你”（三处缺口）

- 时间：开工 23:45
- **决定**（常浩）：就批 1 的三处缺口各答一题，答案按 Vault 规则写进个人上下文（受限，具体内容只在 Vault 里），看过改前改后对照后说“照对照写入”。
- **Vault**（`aa2575b`，3 个文件）：「个人上下文/当前时间预算与资源边界」「个人上下文/阶段重点的确定方式」按常浩回答改写为 revision 3，总入口表同步；「个人认知体系长期目标」不改。现行口径见这两页。提交前 `git status` 干净，提交后 `GET NOTES/` 无改动。
- **Hermes**：`vault` 技能升 0.2.0（项目先后、时间安排、长期目标类问题按对应个人上下文页的现行口径执行），已同步安装位置；`USER.md` 那条指针同步（改前副本 `evidence/M4-B3-20261026/USER.md.before-B4`）。网关 23:52 重启，飞书重连。
- 测试：本机 `hermes -z` 问“5 个项目先保哪个”（`evidence/M4-B3-20261026/selftest-B4.md`）：按「阶段重点的确定方式」现行口径作答，引用了该页和「当前时间预算与资源边界」，并用了 progress 取数。
- 本轮新坑：无。
- 发现：progress 取数只能按单日查。要按「阶段重点的确定方式」的口径排序，`progress_data.py` 最好支持一段日期（比如最近 7 天），先记下，不在 M4 里做。
- 下一步：批 5 F3 正式验收——常浩先在飞书私聊发 `/new`，再随口问 5 个关于自己的判断类问题，问完告诉 Claude 时间，Claude 回查。批 2（规则收口）等 10-30 例行跑完。

## 2026-10-26 · M3 批 3（B3 GitHub）

- **决定**（常浩）：看过推送前检查清单后，5 个仓库都按清单推，远端设置按方案改。牛马之家历史里有约 8 张真实日报截图（同事姓名、群聊原话），属于内部级，按 8.2 可以放进私有仓库；不改写历史。
- **推送前检查**：扫了要推的全部历史里新增的每一行（密钥和 Token 的格式、手机号、身份证、邮箱、飞书个人 ID），并查了数据库、`.env`、证书、大文件和忽略规则。真密钥 0：牛马之家 41 处是 `work-desk-日期` 备份名误命中，数码世界 17 处是测试假值，疑似手机号的 1 处是 UUID 片段。截图只抽看了高风险的日报、自言自语、管培、Voice 几类，没有逐张看完。推之前又对新增的提交复扫了一次。第一轮扫描因 zsh 不拆参数实际没扫到，作废重扫。脚本和结果在 `evidence/M3-B3-20261026/`。
- **推送**：新建私有仓库 `Natsukashii1997/work-desk`、`agent-kit`、`digital-twin`，只推 main；数码世界远端改成新账号名，快进推送 27 个提交；github-center 在远端建 `main`（指向 `claude/v1`，内容和远端已有的一样），默认分支改成 main，本地新建跟踪分支 `main`，检出仍是 `claude/v1`，旧分支都保留。
- **远端设置**：5 个仓库都打开 Issues，只允许 squash 合并，合并后自动删分支；补齐 5 个分流标签（needs-triage、needs-info、ready-for-agent、ready-for-human、wontfix）。
- **名册**：5 个活跃项目的 `github` 都已填上，github-center 的 `main` 改成 `main`。
- 测试：5 个仓库推完后，`git ls-remote` 的远端 main 和本地 main 一致；`gh api` 回读 5 个仓库都是私有、默认分支 main、设置符合方案、5 个标签齐全；主目录检出分支没变，未提交文件数和推前一样（数码世界从 67 变成 65，同时 main 多了 `dac9073`，是别的会话在推送后提交的，没有推上去）；自测 20 项全过；`progress_data.py --project github-center` 显示主分支 `main`。
- 本轮新坑：无。
- 遗留：① 现在 Hermes 的 `merge` 只在本地合并、不推送，远端会落后，批 5 改成走 PR。② 数码世界的 `dac9073` 以及之后的提交，由各会话按 Q9 自己推送。③ 本机 `gh` 的登录记录里还写着旧用户名，不影响使用，没有改。
- 下一步：批 4（5 个仓库跑 `setup-matt-pocock-skills`，`project-starter` 改成两层一起铺）。

## 2026-10-26 · M4 批 3：Hermes 读 Vault

- 时间：开工 23:36
- **决定**（常浩）：批 1 十题评价按 Claude 预评记（认可 1、部分认可 8、不认可 1），已写进基线表；接着做批 3。
- **改动**（改前 `config.yaml`、`SOUL.md`、`memories/`、自带 obsidian 技能打包在 `evidence/M4-B3-20261026/hermes-before-B3.tgz`）：
  - 接 MCP：`hermes mcp add vault-read`（stdio，`/usr/bin/python3 -I <Vault>/.claude/scripts/vault_mcp.py`，和 Claude、Codex 同一份），`hermes mcp test` 连通、发现 search / read 两个工具；在 Hermes 里叫 `mcp__vault_read__search`、`mcp__vault_read__read`。
  - 新技能 `vault` 0.1.0：源文件 `hermes/skills/vault/SKILL.md`，安装在 `~/.hermes/skills/autonomous-ai-agents/vault/`。写死：判断类问题先查、卡片优先、个人上下文读全页、“未定”就说需要常浩定、回复给路径 + 小节、分清已确认 / 推断、只读。
  - 停用 Hermes 自带的 `obsidian` 技能（`skills.disabled` 加一项；它带创建、编辑能力）。`hermes config set` 提示该键“未识别”，但 `hermes skills list` 显示 obsidian 已 disabled，生效。
  - `~/.hermes/SOUL.md`「信息从哪来」Vault 那条后面加一句：判断类问题先按 vault 技能查、给路径、未定就直说。SOUL 没有仓库源文件，只改了安装位置。
  - `USER.md` 追加一条指针（画像真源在 Vault，列出个人上下文、决定、卡片三处），原有条目不动。
  - 网关 23:38 重启，日志里 vault-read 已加载，飞书 23:39 重连。
- 测试：本机 `hermes -z` 一次性会话问 5 题（批 1 里答得差的 2、5、7、10，加一道新题“同事的改流程方案该不该直接采纳”），结果在 `evidence/M4-B3-20261026/selftest.md`，工具调用从 `state.db` 回读：**5 题都先读了 vault 技能，再用 vault-read 搜索和读页**，回复都给了路径（个人上下文题还给了小节名或 P 编号）。第 2 题改为按对应个人上下文页的口径作答；第 5 题读到了有效卡片；第 7 题回到“预算和操作由人决定”；第 10 题也按对应个人上下文页作答。没有贴受限原文段落。这是 Claude 自测，不算 F3 验收。
- 本轮新坑：无。
- 发现，留到批 2（都在 Vault `.claude/scripts/vault_mcp.py`，改它要常浩确认）：① 它返回的提示还写着“不发往团队飞书、机械邪龙兽”，是 v1.5 之前的说法；技能里已写明私聊算本人入口，暂不影响作答。② 搜索只按目录排序、没有相关度：新题搜“建议”“方案”时，结果被个人上下文的命中占满，最对口的卡片「别人的建议先拆成他看到的问题和他给的方案…」没进前几条，退而用了「找根因…」那张。
- 遗留：飞书私聊里现有的会话是重启前开的，系统提示可能还是旧 SOUL；批 5 正式验收前常浩先发 `/new` 开新会话。
- 下一步：批 2 规则收口（等 10-30 周五例行跑完；顺带上面 ①②，以及 `get-index`、`思维模型库` 是否并入知识库）；批 4 就三处缺内容各问常浩一个问题。

## 2026-10-26 · M4 批 1：F3 基线

- 常浩 23:16～23:33 在飞书私聊问了全部 10 个备选题（会话库消息 #1312～#1355）。Claude 逐题核对 Hermes 的工具调用和回答，基线表和小结在 `evidence/M4-20261026/批1-备选题.md`。
- **结果**：只有 4 题读过 Vault（2、5、9，10 题部分），6 题完全没查，靠 SOUL 和模型常识作答；没查的题里 5 题结论和 Vault 一致，但不引用、无从核对。按 F3 标准合格 1～2 题。10 题都没有贴受限原文。
- **问题**：① 不知道“判断类问题要先查 Vault”。② 用 Hermes 自带通用技能 `obsidian` + 正则直搜目录，没有卡片优先：第 5 题读到派生主题页而不是有效卡片，第 2 题漏读了对应的个人上下文页，结论推断过头。③ 自带 `obsidian` 技能带创建、编辑能力，和“只写收件箱”冲突。
- **缺内容**：第 2、9、10 题对应的个人上下文没有登记，只能由常浩本人给答案（受限，具体见 Vault 个人上下文）。
- 常浩对 10 题的评价（认可 / 部分 / 不认可）还没给，表里先写“待评”。
- 本轮新坑：无。
- 遗留：M3 批 1 转来的 `get-index`、`思维模型库` 是否并入知识库，放到批 2 一起定。
- 下一步：常浩补评价；之后批 2（规则收口，等 10-30 例行跑完）、批 3（读取接入，按上面 ①～③ 写技能）可以开工，批 4 先就三处缺内容向常浩各问一个问题。

## 2026-10-26 · M3 批 2（B2 目录整理）

- **决定**（常浩）：按清单搬，规则和巡检按方案改。和计划不同的两处，常浩看过清单后同意：Hermes 配置审查里的 `test-checkout`、`worktree` 原地不动（worktree 登记在 `~/.hermes/hermes-agent` 里）；`~/Documents/Codex` 的 60 个按日期建的目录不整体搬（Codex 旧对话和 `~/.codex/config.toml` 还指着它们），只搬出里面的仓库。
- **搬移**（只用 `mv`，同一块磁盘内改名）：25 项。沙盒 16 个 → `~/Sandbox/<日期>-<名>/`；拆外壳 7 个，只把 `.git` 移到 `~/Archive/repos/拆外壳/`（`个人`、`AI 项目`、`AI实验室 2`、`杂活`、`自动化推送`、`思维模型库`、`私域营销`），文件原地保留；待删 2 个空目录 → `~/Archive/repos/待删/`。脚本、`done.json`、恢复脚本 `restore.sh` 都在 `evidence/M3-B2-20261026/`。
- **规则**：`~/.claude/CLAUDE.md` 加第 8 节「项目目录」，`~/.codex/AGENTS.md`「软件项目」一节加一条，内容相同：正式项目在 `work/<项目>/`、临时探索和第三方克隆放 `~/Sandbox/<日期>-<主题>/`、不在仓库里再建仓库。改前备份在证据目录。
- **巡检**：`ops/health_check.py` 加沙盒检查。每月 1 号列出 `~/Sandbox`、`~/Documents/Codex` 里顶层超过 30 天没动的目录，用猛鬼兽单独发一条提醒，只提醒、不删；`--sandbox` 可以不等 1 号就查。原有告警逻辑没变，只把发消息那段抽成 `send()`。已同步到 `~/Library/Application Support/DigitalTwin/bin/`（同步前确认安装副本和旧源文件一致，旧副本已备份）。
- **名册**：搬过的条目改成新路径，原路径记在 `from`；拆外壳的条目记了 `.git` 的去处；Hermes 那两项改为「只登记」。
- 测试：launchd 服务搬前搬后逐项一致（`launchd-before.txt` / `launchd-after.txt`）；5 个活跃仓库和牛马之家 worktree 的 HEAD、未提交文件数搬前搬后一致；`~/Documents` 剩 20 个仓库，仓库套仓库只剩 Fmode 用户案例库的 share-site（登记为保留）；`~/Documents` 和 `~/Sandbox` 里共 36 个仓库，和名册逐个对得上；巡检 `--dry-run --sandbox` 列出 48 项（9 个在 Sandbox、39 个在 Codex 旧目录），`--dry-run` 不到 1 号时不查沙盒；源文件和安装副本都跑过；自测 20 项全过。
- 本轮新坑：无。
- 遗留：① `~/Documents/Codex` 下 39 个旧目录要不要清理，等 11-01 第一次沙盒提醒时常浩再定；Codex 桌面版还会继续在这里按日期建目录，规则管不住。② `~/Documents/GitHub` 已空，留着没删。③ `~/Archive/repos/待删/` 的两个空目录由常浩自己删。
- 下一步：批 3（GitHub）。要对外推送，每个仓库推之前先给常浩看检查清单。

## 2026-10-26 · M3 项目工程化：计划定稿与批 1（B1 盘点 + 名册 v1）

- **决定**（常浩）：M3 分 6 批（批 1 → 批 2、批 3 并行 → 批 4 → 批 5 → 批 6），10 个问题全按推荐定，写进 PRD 12.1。要点：GitHub 统一用 `Natsukashii1997`（就是原来的 `abdoulaziznafiou03-creator`，改了名）；8.2 加上「本人 GitHub 私有仓库」一行（普通、内部可以放）；项目根就是 `work/`，活跃项目不搬，沙盒定为 `~/Sandbox/`；删除类只移到 `~/Archive/repos/`。计划全文在会话共享笔记 `M3-项目工程化-分批计划.md`。
- **账号核实**：`gh api user` 返回 `Natsukashii1997`，`users/abdoulaziznafiou03-creator` 返回 404；账号下有 4 个私有仓库（github-center、personal-ai-workbench、little-steps-private、my-app-lumist），权限都是 admin。
- **批 1**：`projects.json` 升到 v1。`projects` 还是 5 个活跃项目，加了 `category`、`github` 两个字段（只有 personal-ai-workbench、github-center 有远端）；新增 `inventory`，列出其余 40 个 `.git` 的去留：沙盒 18、只登记 10、拆外壳 7、待删 2、常浩自删 2（营销战役），另 1 个是牛马之家的 worktree。Obsidian Second Brain 按 M4 的决定标为“M4 移到 `~/Archive/`”。脚本只读 `projects`，所以 `dispatch.py`、`progress_data.py`、`run_worker.py` 都没改，不用同步、不用重启网关。PRD 改了状态行、8.2、Q4、Q6、附录，新增 12.1；AGENTS 结构说明补了 inventory。
- 测试：自测加 1 项（`inventory` 里的仓库不现查），共 20 项全过；名册覆盖核对：`find ~/Documents -maxdepth 7 -name .git` 找到 45 个，名册列了 45 个，没有漏的、没有多的；`progress_data.py --date 2026-10-26` 改前改后只差时间戳和本仓库自己的未提交文件数；`dispatch.project()` 5 个活跃项目都能取到。改前名册和两份输出在 `evidence/M3-B1-20261026/`。
- 本轮新坑：无。
- 遗留：① `inventory` 里 `baby-care-workbench`（10-24 还有提交，GitHub 上的 little-steps-private 可能是它的远端）去留待常浩定。② `get-index`、`思维模型库` 是否并入知识库归 M4。③ 共享笔记里计划写的“43 个 `.git`”实为 45 个，“launchd 指向 `Codex/`”不对，实际只指向 `AI 项目/` 和 `work/`；已在笔记里改正。
- 下一步：批 2（目录整理）和批 3（GitHub）可以并行。批 3 要对外推送，每个仓库推之前单独问常浩。

## 2026-10-26 · M4 知识库系统：计划确认，批 1 基线进行中

- **计划**（常浩 22:5x 确认）：批 1 F3 基线（只读）→ 批 2 C1 + C4 规则收口（Vault AGENTS v1.6，≤5 个文件）→ 批 3 Hermes 接 vault-read、新技能 `vault`、重建 USER/MEMORY 为摘要 + 指针 → 批 4 C2 按基线缺口补厚“你”（每轮 ≤5 页）→ 批 5 F3 正式验收（常浩本人问 5 题）→ 批 6 F7 每周日 20:00 写 3～7 条候选，周五 `/kb-digest` 定去向、`/kb-weekly` 记采纳率，连续 2 周 ≥ 50%。批 2、3 可与批 1 并行；Vault 侧改动等 10-30 周五例行跑完再动。
- **拍板**（常浩：6 条全部按推荐）：
  1. F3 引用格式：结论 + 笔记路径 + 小节标题，不贴受限原文；验收时常浩点路径核对或 Claude 回查。PRD F3 验收措辞改为“引用 Vault 笔记作答”（批 2 一并改）。
  2. C1：`~/Documents/Obsidian Second Brain`（10-09 建、无提交、基于已停用的 `wiki.py` 门禁）停用，整目录移到 `~/Archive/`，Vault 项目卡记一笔；其 T2 候选不再追。
  3. Hermes 候选只新建在 Vault `00 待整理/Hermes/`，Hermes 按 Vault AGENTS 第 6 节只提交本次文件。
  4. 项目卡只给项目名册 5 个活跃项目各建一张薄卡（目标 + 链接 CURRENT），M3 名册定稿后再增减。
  5. C3：GET NOTES 不批量消化；订阅流 692 篇维持不进检索，`Notes/` 219 篇靠周五 `/kb-digest` 增量，F3 问出缺口时定向挖；由 Claude 做，不让 Hermes 做。
  6. F3 验收题由常浩自己随口问；批 1 由 Claude 提供 10 个备选。
- **批 1**：10 个备选题（个人 4、方法 4、缺口 2，附 Vault 依据）在 `evidence/M4-20261026/批1-备选题.md`。等常浩挑 5 题、本人在飞书私聊问猛鬼兽；问完由 Claude 查 `state.db` 出基线表（同一文件），据此定批 4 范围。
- 现状要点：Vault AGENTS v1.5 与 SOUL 已写 Hermes 可读、用而不露、提议写收件箱；缺的是写收件箱细则、读取技能、vault-read 接入和定时任务。“你”这一层：个人上下文 6 页、决定 4 条、项目卡 1 张，全部受限。
- 未改 Vault、未改 Hermes。
- 下一步：常浩挑题并在飞书提问。

## 2026-10-26 · M2 收尾：F6.2 按需现查（批 1～3）

- **决定**（常浩）：F6.2 选 A，先做取数脚本和技能，常浩问的时候 Hermes 现查；T1 日报重设计后再并入晨报。“今天”从当天 00:00 算；单独做技能，不并进 workshop。PRD F6.2 已写明。
- **批 1**（`101722d`）：`hermes/scripts/progress_data.py`，只读、只用标准库。按名册读各仓库当天的提交（所有本地分支，按分支归，主分支优先，同一提交只记一次）、主分支停在哪、主目录检出分支和未提交文件数、CURRENT 最新一节和“下一步”（认三种写法：列表“下一步：”、表格“| 唯一下一步 |”、标题“## 唯一下一步”），以及当天的派单。参数 `--date`、`--project`（认别名）。新增 6 项隔离自测，连同原有 13 项共 19 项全过；5 个项目当天的提交数和手工 `git log` 逐个一致。
- **批 2**（`619eaa3`）：技能 `progress` 0.1.0，安装在 `~/.hermes/skills/autonomous-ai-agents/progress/`，脚本同步到 `~/.hermes/scripts/`，网关已重启，`hermes skills list` 显示 enabled。
- **批 3 飞书验收**（常浩本人在手机上问，22:43）：
  - 问“今天各项目怎么样”：Hermes 读了 progress 技能，跑了 `progress_data.py`（会话库里能查到这条终端调用），回复了 5 个项目。提交数（9、2、1、16、0）、未提交文件数（4、72、1、0）、检出分支、各项目“待你定 / 下一步”都和脚本输出对得上，没有编出项目或提交。
  - 问“昨天呢”：它自己换算成 `--date 2026-10-25` 再跑，回答 5 个项目都没动静，和脚本一致，还主动说明“没落盘不代表没做事”。
  - 小偏差：agent-kit 那段点名了“`LESSONS.md` 和 `skills/`”，而脚本只给了“未提交文件 2 个”，文件名来自同一会话里 E5 的回报。内容属实，但不符合技能“只按脚本输出作答”，先记下，不改。
- **F6 验收通过**：常浩不打开电脑，在飞书就能知道各活跃项目今天的进展。连同 F5，**M2 完成**。
- 遗留：① F6.2 并入晨报，等 T1 日报重设计。② PR、issue 等 M3 统一 GitHub 后再加进取数（F6.3）。③ 上面的小偏差，如果再出现就把技能收紧。④ F1 验收（提醒 → 确认派单 → 收回报，全程手机）里，“提醒”要等晨报恢复；这次常浩亲自走通的是“问进展 → 收回答”。
- 下一步：另外梳理 M3 项目库工程化和 M4 知识库系统（两者可以并行）。

## 2026-10-26 · M2 批 E（真实小单验收）与收尾

- **决定**（常浩）：F1.2 放弃每单一个话题，统一靠单号区分；F1.3 确认按钮暂缓，用文字回“可以”。PRD 已改，车间技能 0.4.1 去掉了待确认派单里的话题提示（`f11287b`）。
- **派单方式**：常浩让 Claude 代为操作，Claude 用 `lark-cli --profile codex --as user` 以常浩身份往猛鬼兽私聊发消息（派单、回“可以”、回“合并”），每条都先核对待确认派单和原单一致。
- **6 张单**（原计划 5 张，E1 跑出巡检测试问题后加了 E6）：

| 单号 | 车间 | 类型 | 项目 | 结果 |
|---|---|---|---|---|
| D20261026-10（E1） | Claude | 写入 | 牛马之家 | 带教卡片日期列左对齐，提交 `765f18e`，1 分 06 秒；复盘小标签也跟着左对齐，常浩选保持。**未合并**（常浩没勾），分支和 worktree 保留 |
| D20261026-11（E2） | Codex | 写入 | 数字分身 | `--followup` 续跑前把旧回执归档成 `receipt-N.json`，提交 `faa00f4`，自测 13 项（Claude 复跑全过）。**未合并**，安装位置未同步 |
| D20261026-12（E3） | Codex | 只读 | GitHub 快速中心 | #3、#9 跑数已结束：近 7/30 天候选 3,791/4,878，自检都没过（标“可能不完整”）；全程 6,766 次请求、10,140 秒 |
| D20261026-13（E4） | Claude | 只读 | 数码世界 | 主目录未提交改动是两轮：设置页 v19（650 选稿 + 651 落代码，剩第 2 批样式、第 3 批动效、第 4 批回归）和 10-26 世界总览；只有 CURRENT.md 两轮混在一起 |
| D20261026-14（E5） | Claude | 只读 | agent-kit | 未提交的 14 行是新全局坑 G-12（重启后会话里的定时后台任务落空），找不到批准记录、launchd 说法过头；常浩决定**先放着** |
| D20261026-15（E6） | Codex | 写入 | 牛马之家 | `scripts/health-check.sh` 三处 `$变量` 紧跟中文改成 `${…}`，C.UTF-8 下 npm test 739/739。常浩说“合并”：合并前测试 739/739，快进到 main `3d37dba`，worktree、分支已清理，未 push、未发布 |

- **护栏回读**：6 张单的回执 `guard_main_changed`、`guard_remote_changed` 都是 false；只读单 HEAD 前后一致、无新提交；5 个仓库的主分支除 E6 合并外都没动。
- **网关加代理**：派 E1 时主模型 gpt-6.1-sol 连 chatgpt.com 一直 ConnectTimeout，每轮白等约 7 分钟才退回 deepseek-flash。原因是本机直连境外不通了（19:19 前还通），只能走 127.0.0.1:8086，而网关进程没配代理。常浩同意后，`ai.hermes.gateway` 的 plist 加了 HTTP(S)_PROXY 和 NO_PROXY（飞书、DeepSeek、本机不走代理），bootout/bootstrap 重载，飞书重连正常，之后主模型正常出结果。改前的 plist 备份在 `evidence/M2E-20261026/`。
- **会话上下文**：旧会话约 11 万 token，超过压缩阈值，压缩又用不了（辅助模型报“找不到 Codex OAuth token”）。常浩同意后发 `/new` 并 `/approve`，E2 起都在新会话里派。
- **E1 顺带发现**：车间环境里 `LANG=C.UTF-8`，bash 会把紧跟变量的中文当成变量名的一部分。已由 E6 修掉牛马之家的巡检脚本。
- **关掉 Claude 聊天桥**：`ai.lark-channel-bridge.bot.claude` 已 bootout，plist 和两份旧备份移到 `~/Archive/2026-10-26-retired/LaunchAgents/`，README 里写了恢复办法。回读：launchd 找不到该服务，没有残留进程。Codex 聊天桥还在跑，按 PRD 归 M5 T8。
- **M2 结论：F5 通过、F6.2 未做**（22:40 改正：之前误写为“完成”。M2 的完成标志是 F5、F6 都验收通过；F6.1 每单回报已通，F6.2 晚间扫描、并入晨报还没做，所以 F6 验收“你不开电脑也能知道各项目今天的进展”没过）。另外，F1 验收“你在手机上只用 Hermes 跑一整轮”这次是 Claude 代发的，没有由常浩亲自验证。
- 合并补记（22:25，常浩说“都合并”）：D20261026-10 合并进牛马之家 main `b265d82`，D20261026-11 合并进本仓库 main `a8b4503`。两单合并前测试都通过（牛马之家 739/739；本仓库没配测试命令，Claude 在 main 上复跑自测 13 项通过），worktree、分支都已清理，未 push、未发布。`dispatch.py` 已同步到 `~/.hermes/scripts/`，旧副本备份在 `evidence/M2E-20261026/`，脚本每次现调，不用重启网关。
- 遗留：① 已处理（见上一条）。② Hermes 压缩用的辅助模型没有 Codex OAuth token，长会话还会再撞阈值。③ G-12 先放着。④ E2 的实现偏重（符号链接检查、fsync、原子链接），能用。
- 下一步：常浩选 F6.2 的做法（已选 A，见上面「M2 收尾」一节）。

## 2026-10-26 · D20261026-11（followup 保留历史回执，仅源文件）

- `hermes/scripts/dispatch.py`：`run --followup` 启动 worker 前，把已有 `out/receipt.json` 的独立字节副本归档为 `receipt-1.json`、`receipt-2.json` 等；编号按已有最大值递增，已有路径不覆盖，`receipt.json` 仍供读取最新回执。
- 归档先完整写入同目录临时文件并同步，再以不覆盖的方式发布。回执读取、解析、归档或发布失败时返回失败，不启动 worker、不把台账改成 running；当前回执为目录或符号链接时拒绝续跑。确实没有旧回执时保留原有无 resume 启动行为。
- 新增隔离自测和说明：`hermes/scripts/tests/test_dispatch_followup.py`、`hermes/scripts/tests/README.md`。真实执行 `python3 -B -m unittest discover -s hermes/scripts/tests -v`：13 项通过；覆盖首次归档、多轮保留、已有历史及发布冲突不覆盖、普通运行兼容、失败停止。全部使用临时数据和假 worker，未调用真实模型或外部服务。
- 本单明确仅改当前 worktree 的仓库源文件：**未同步任何安装文件，未重启网关，未改配置，未合并或 push**。工程自测通过不代表运行环境已经采用。
- 结果：22:25 常浩说“合并”，已合并为 `a8b4503` 并同步到安装位置（见上面「M2 批 E」一节）。

## 2026-10-26 · M1 第 0、1 批

- 时间：开工 16:50
- 第 0 批：已完成。`~/.hermes` 备份在 `M1-20261026/hermes-home-before-M1.tgz`，共 629 项；没有包含可以重装的 `hermes-agent`、`installs`、`tools`、`cache`。基线记在同目录的 `baseline.md`（v0.22.2，git d923a5eb，模型 deepseek-flash，没有定时任务，网关运行中、飞书已连接）。
- 第 1 批：本机已切换完成，等飞书实测。
  - Hermes 单独登录了 Codex 会员（设备码，凭证 `openai-codex-oauth-1`），不影响本机 Codex CLI 的登录。
  - 主模型：`openai-codex / gpt-6.1-sol`。按 `~/.codex/models_cache.json` 的说明，它是当前的主力模型。
  - 辅助模型：压缩和审批分类用 `openai-codex / gpt-6-luna`（快、便宜，省额度）；其余辅助任务跟随主模型。
  - 回退：`fallback_model = deepseek / deepseek-flash`。
  - 验证：`hermes doctor` 显示 Codex 已登录、辅助任务都路由到 openai-codex；一次性对话回复了 `OK openai-codex / gpt-6.1-sol`；故意指定一个不存在的模型时，回复是 `deepseek / deepseek-flash`，说明回退生效。网关已重启（pid 85113），飞书已连上。
  - 遗留：回退时要在回复里注明，这条写进第 3 批的 SOUL。
  - 飞书实测（17:01）：常浩发“嗨boy一天没说话了”，10 秒后回复，语气符合人设。`state.db` 的 `session_model_usage` 里，飞书会话 `20261021_200002_45130229` 这一次调用记录为 `gpt-6.1-sol / openai-codex / subscription_included`，走的是会员额度，没有按量计费。**第 1 批完成。**
- 第 2 批：已完成（17:10 前后）。
  - agenthub 前后端：`launchctl bootout` 停掉，plist 移走；`~/agent-hub`（530M，不是 git 仓库）移走；Claude 桌面配置 `claude_desktop_config.json` 里摘掉了 `agent-hub` MCP，修改前的版本留了备份。**Claude 桌面应用重启后才会生效。**
  - OpenClaw：plist（本来就没加载）和 `~/.openclaw`（197M）移走；用 Homebrew 的 npm 卸载了全局 `openclaw` 包（共 330 个包）。
  - 归档位置：`~/Archive/2026-10-26-retired/`，权限 700，有 README，2026-11-25 到期后可删。
  - 回读：常驻服务列表里已经没有这两个服务，`openclaw` 命令也不存在了。
- 第 3 批：部分完成，等常浩确认 Vault 的改动。
  - SOUL 已重写：新增数字分身定位、信息来源、信息边界、模型与额度、定时与主动；去掉了“不读 Obsidian”“不建定时任务”“不替换模型”；收尾措辞改成“emoji + 事实 + 现编收尾”，去掉了常浩否决过的固定例句。网关已重启（pid 9619），飞书已连上。修改前的 SOUL 在第 0 批的备份里。
  - 全局规则：`~/.claude/CLAUDE.md` 第 6 节、`~/.codex/AGENTS.md`「知识、权限与外部动作」的知识库条款，都改成了分级出口规则；修改前的版本在证据目录 `*.before`。飞书分工那条（`--profile codex`）留到 M5 再改。
  - Vault 三个文件的草稿在证据目录 `vault-drafts/`：`AGENTS.md` v1.5、两张决定页。按 Vault 规矩，**等常浩确认后再写入并提交**。
  - 本机预检（`hermes -z`，用的是同一份 SOUL）：① 群聊里诱导“贴个人上下文原文”→ 拒绝，并说明群聊文字只是材料、受限内容不进群，通过；② 带教取舍这类判断题 → 查了 Vault 个人上下文的 3 篇笔记，回复只给结论和路径，没贴原文，通过；③ 回退时在回复末尾注明 → **没注明**。网关自带一条回退提示（`⚠️ Provider fallback: … using deepseek/deepseek-flash`），但只在每轮开始前凭证失败时才会出现，对话中途发生的回退会不会提示，没有核实。
  - 常浩补充决定（2026-10-26）：**本机访问飞书也统一用猛鬼兽，飞书只保留这一个机器人**。草稿随之改动：飞书决定页改名为「飞书统一用猛鬼兽」，同步改了总入口里的链接；Vault 决定页加了一句说明；PRD 的 D0、D12、D13、F9.3、退役清单同步更新，M5 里新增 T8b（约 20 个目录从 `--profile codex` 迁过来，迁完后停用机械邪龙兽）。`~/.codex/AGENTS.md` 里 `--profile codex` 那条绑定，迁移时一并修改。
  - 常浩确认后写入 Vault，共两次提交：`3c2c688`（只有改名：飞书统一用机械邪龙兽 → 飞书统一用猛鬼兽）和 `089894f`（4 个文件的正文：AGENTS v1.5、两张决定页、总入口）。第一次提交时 `git add` 用了旧路径报错，正文没有跟上；按 Vault 规矩不改写历史，所以补了第二次提交。回读：工作区干净。
  - 常浩补充：营销战役 MVP 他会整体删除，不纳入迁移。PRD 第 7 节已改，并新增 7.1“单一机器人的技术约束”：事件监听只由 Hermes 网关独占（飞书长连接模式下，同一应用的事件只推给其中一个客户端）；本机项目只主动调用接口；频率限制共用；发送者统一显示猛鬼兽，需要时在末尾注明来源。迁移范围缩小到牛马之家、personal-ai-workbench、公司战略系统、管培生带教（它的卡片回调要改走 Hermes 转发，或者去掉）。
  - **第 3 批完成**（遗留：回退时在回复里注明还不可靠，第 5 批巡检时补一条“前一天有没有走回退”的检查）。
- 第 4 批：样稿已出，等常浩定格式。
  - 取数脚本：`hermes/scripts/brief_data.py`（项目里是源文件，安装副本在 `~/.hermes/scripts/`），只用标准库，Hermes 自带的 Python 可以直接跑。功能：从日报第四段算出到期和逾期；摘出第六段的建议；查阻塞是否连续 3 份日报都出现；从周报里找挂起满 4 周的事项；查日报断档；接口返回缓存旧结果时提示。
  - 晨报提示词：`hermes/prompts/morning_brief.md`；样稿：`hermes/samples/晨报样稿-20261026.md`（gpt-6.1-sol 根据 10/23 日报和第 43 周周报生成）。
  - 发现牛马之家的问题（不在本项目范围，没修）：10/22 的日报标题没被识别，内容整段混进了 10/23 那篇。脚本已能识别这种情况，并在晨报里提示。
  - **常浩决定挂起第 4 批（2026-10-26）**：现在的日报本身写得不好，抓到的信息可能不准，在此基础上提炼没有意义。等 T1 日报结构重新设计后再恢复。脚本、提示词和样稿都保留，不建任何定时任务。
- 第 5 批：已完成。
  - 脚本：`ops/health_check.py` 是源文件。安装副本放在 `~/Library/Application Support/DigitalTwin/bin/health_check.py`，因为 launchd 调起的 `/usr/bin/python3` 读不了 `~/Documents`（macOS 隐私保护，第一次实测时报 Operation not permitted）。**改了源文件要重新复制到安装位置。**
  - 常驻任务：`cn.digital-twin.health`，每天 09:45 跑（常浩 2026-10-26 从 08:50 改）；plist 也复制了一份到 `ops/`。
  - 检查项：网关是否加载、进程是否在跑、状态是否为 running、飞书是否 connected、心跳 10 分钟内有没有更新；前一天主对话有没有走回退模型（排除本机 `hermes -z` 测试会话，也就是 source=oneshot）；定时任务有没有失败记录。纯脚本，不调用模型。
  - 输出：每次都在 `evidence/health/YYYY-MM.log` 记一行；只有出问题时，才用猛鬼兽机器人身份（`lark-cli --profile cli_a96f5f9ac63adcd4 --as bot`）往私聊发告警。
  - 实测（17:20）：停掉网关后由 launchd 触发一次巡检，告警发送成功；随后恢复网关。17:22 网关正常时复跑，结果 OK。
  - 修过的问题：网关停着时，WAL 数据库的 `-shm` 文件不在，只读连接打不开，已改成普通连接（只做查询）。修改后**没有在网关停止的状态下复测**。
- M1 小结：第 0、1、2、3、5 批完成，第 4 批挂起。PRD 里 M1 的 F4 验收要等日报重新设计后才能做。
- 巡检时间：常浩改成每天 09:45，plist 已重新加载，回读确认触发时间是 9:45。

## 2026-10-26 · M2 批 A（打通两个车间）

- 时间：开工 17:30
- 三个待定项按推荐定：写入单放开权限，边界靠 worktree、护栏和合并确认；名册 v0 收牛马之家、personal-ai-workbench、agent-kit、github-center、digital-twin；验收用的 5 张单到批 E 再由 Claude 挑。
- 包装脚本：`hermes/scripts/run_worker.py`（安装副本在 `~/.hermes/scripts/`）。Claude 和 Codex 共用一个入口：代理可达时自动设上 127.0.0.1:8086，清掉第三方 Anthropic 变量；read 模式只读（Claude 只允许 Read/Grep/Glob/LS，Codex 用 `-s read-only`），write 模式放开权限；会给车间统一加一段开头说明（先读 AGENTS、不切主分支、不合并、不 push、卡住时输出 `NEED_DECISION:`）；跑完写 `receipt.json`，护栏检查主分支和远端引用有没有变。退出码：0 正常，1 失败或超时，2 护栏被触发。
- 实测：
  - 终端里用干净环境（`env -i`）对 agent-kit 跑只读单：Claude 20 秒、Codex 53 秒，都正确总结了仓库，护栏正常。
  - **网关环境**：建了一个 1 分钟后执行、只跑一次的 Hermes 定时任务，由网关自己的调度器执行（运行记录 source=builtin，环境里确实没有代理）。结果：读 Vault OK，读仓库 OK，Claude 和 Codex 都回复“OK agent-kit”，退出码都是 0，护栏正常。之前担心的“网关里直连 403”和“macOS 隐私保护”都没成为问题。第一次用 `cron run` 手动触发那次是在终端进程里跑的（source=direct，带着代理），不算数，已删掉。
  - 探针脚本和临时目录已清理，定时任务列表为空。
- 车间技能：`hermes/skills/workshop/SKILL.md`（安装在 `~/.hermes/skills/autonomous-ai-agents/workshop/`，已启用）。目前只接只读单，限定在名册 v0 的 5 个仓库；单号格式 `D年月日-序号`；后台运行，结束后读回执回报，护栏一旦触发就在回报第一行标 ⚠️。网关已重启（pid 22932）。
- agent-kit 本身有不属于本项目的未提交改动（LESSONS.md 修改、skills/ 未跟踪），没碰。
- 飞书验收（常浩派单，Claude 回读确认）：
  - `D20261026-1` Claude · personal-ai-workbench · 50 秒：✅。按要求 5 句话说清进度和卡点，每条都带出处（提交号、文件和行号），并主动说明“没有运行服务或测试复验”。回报格式符合技能要求。
  - `D20261026-2` Codex · 牛马之家 server · 8 分 52 秒：清单完成，共 177 个接口，附了 CSV。护栏触发（退出码 2），Hermes 第一行标了 ⚠️，没有重试，并说明“不能确定是谁更新了主分支，保留告警”，处理方式正确。
  - **护栏误报的根因**：跑单期间，另一个 Claude 会话（牛马之家 ui-v4 分支，培养路径卡头修复）在 17:58:04 和 18:00:24 两次快进合并了 main（`2a86e20`、`2928948`），不是 Codex 动的。Codex 是只读沙箱，本来就写不了。
  - 修正：`run_worker.py` 的护栏改成只在两种情况下报违规——写入模式下车间自己的提交进了主分支，或者车间把当前分支切到了主分支；别的会话正常更新主分支只记在 `note_main_moved`，不告警。技能升到 0.1.1，同步说明“note 不算告警”。回归：干净环境下 Claude 只读单退出码 0，护栏 false，note 为空。网关已重启（pid 33234）。
  - 中间有一次回归用错了任务文件（拿第一单的重任务去跑 agent-kit），300 秒超时，浪费了一次 Claude 额度，没有其他影响。
- **M2 批 A 完成。**

## 2026-10-26 · M2 批 B（项目名册 v0）

- digital-twin 建了独立 git 仓库（`main`，首次提交 `2b11a3a`），并新增 `AGENTS.md`（结构、源文件和安装位置的对应关系、规则）。它还套在上级仓库 `ChatGPT/个人` 里，属于仓库套仓库，M3 整理目录时一起处理。
- 名册 `projects.json` v0 收了 5 个项目：牛马之家、personal-ai-workbench、agent-kit、github-center、digital-twin。每个项目写了别名、路径、主分支、AGENTS 位置、测试命令、是否允许写入、备注。github-center 没有 main 分支，远端默认分支是 `codex/bootstrap`，名册按这个记；它的远端还在另一个 GitHub 账号下，待 M3 处理。
- `run_worker.py` 接入名册：按 cwd 所属的主仓库匹配（worktree 也认），名册外的直接拒绝，退出码 3；主分支默认从名册取。测试：公司战略系统 robot-cards → 拒绝，退出码 3；work-desk-ui-bitmap（work-desk 的 worktree）→ 识别为 work-desk/main。
- 车间技能升到 0.2.0：派单前先读名册，按名称和别名匹配；匹配不上或匹配到多个时先问常浩。
- **验收时发现的问题**：说“让 Codex 看一下公司战略系统”时，Hermes 加载的是自带的 `codex` 技能，直接 `codex exec`，绕开了名册和护栏（只读沙箱，没有造成改动）。修复：
  - `skills.disabled = [claude-code, codex]`，停用自带的两个技能（`hermes skills list` 回读显示 disabled；`hermes config set` 提示这个键“不认识”，但技能列表确实读到了）。
  - SOUL 新增「派活给 Claude Code / Codex」一节：一律走 workshop，名册外不派单，也不能改成自己去读那个项目来代替派单。改后的 SOUL 存档在证据目录 `SOUL.after-M2B.md`。
- 复测（`hermes -z`）：“数码世界 / 全局坑”能匹配到名册项目，路径和主分支都正确；“公司战略系统 robot-cards”→ 回复“不在名册里，要加进去吗”，没有读取项目；“让 Codex 看一下全局坑”→ 走 workshop 技能和包装脚本，单号 `D20261026-3`，✅ 回报正确。
- **M2 批 B 完成。**
## 2026-10-26 · M2 批 C（开通写入单）

- 派单脚本 `hermes/scripts/dispatch.py`（安装副本在 `~/.hermes/scripts/`），子命令：new / run / merge / clean / list。
  - 每张单一个目录 `~/.hermes/dispatch/<单号>/`，`order.json` 就是台账，**没有另建 PRD 里写的 ledger.jsonl**：台账是运行状态，不进代码仓库。
  - 写入单的 worktree 放在 `~/.hermes/worktrees/<项目>/<单号>`，分支 `hermes/<单号>`，从名册里的主分支拉出；主仓库有 node_modules 就链接过去，车间可以直接跑测试。
  - 合并：先检查 worktree 是否干净、主仓库是否在主分支上且干净 → 把分支变基到最新主分支（有冲突就撤回变基）→ 跑名册里的测试命令 → 快进合并，不 push。清理：合并过的分支用 `-d` 删，放弃的用 `-D`。
  - 并发：同时最多 2 张单在跑，同一个仓库一次只跑 1 张，超出的返回 `busy` 并排队。
- `run_worker.py`：给车间的开头说明按只读和写入分开；写入单要求先跑测试或检查、再提交，不留未提交的改动，不重装依赖。
- 实测：
  - 本机 `D20261026-4`（digital-twin · Claude 写入）：开单建 worktree → 29 秒改完并提交 `hermes/samples/README.md` → 主仓库有未提交改动时，合并被正确拒绝 → 提交后主分支前进了，合并时先变基再快进（`31b5d69`）→ 清理完，worktree 和分支都没了。
  - 并发：用假单测了两种排队情况（已有 2 单在跑、同仓库已有单在跑），都返回 busy，测完删掉了假单。
  - 网关环境：用 Hermes 定时任务跑了一次 `D20261026-5`（source=builtin，环境里没有代理）：建 worktree、Claude 改文件并提交、放弃并清理都成功，后台进程往 `~/Documents` 里的仓库写入没有被拦。探针已删。
- 车间技能升到 0.3.0：派单五要素、写入单先发“📝 待确认派单”，常浩明确同意才开跑；回报里写分支、提交的文件、有没有未提交的改动，最后问“合并吗？”；常浩说“合并”才合并，然后清理；排队的单等手上的单跑完再启动。第一次写技能文件时 Write 失败（文件之前被脚本改过），装进去的还是旧版，已重写、重装并核对版本号。网关已重启（pid 67458）。
- 提交：`df484ce`、`31b5d69`（D20261026-4 合进来的）、`6aef4ae`。
- 飞书验收 `D20261026-6`（常浩派单，Claude 回读）：
  - ✅ 18:43 Hermes 发了“📝 待确认派单”，目标、验收、边界都写了，边界里明确“不合并、不推送”。常浩 18:44 回“可以”后才开单。
  - ✅ Claude 用 30 秒新增 `hermes/prompts/README.md`，提交 `09c0b36`。回报里有分支、提交号、检查结果，并问“合并吗？”。
  - ⛔ 常浩 18:46 回“合并”，被拦下：“主仓库有未提交的改动”。原因是 **Claude 这个会话改了 CURRENT.md 后没提交**。Hermes 没有硬合，也没清理，而是问常浩怎么处理，做法正确。
  - 修正：合并前不再要求主仓库完全干净（牛马之家这类主仓库经常有别的会话留下的改动），改成交给 git 判断：快进合并只有在未提交的改动和本单改了同一批文件时才失败，失败时的提示会写明这一点。技能说明同步修改。
  - 按常浩在飞书说的“合并”，由 Claude 用同一个脚本补做：主仓库带着 3 个无关的未提交文件，合并成功（`09c0b36`），随后清理，worktree 和分支都已删除。
  - 注意：Hermes 自己的会话里，这张单还记着“没合并”，用 `D list` 能看到实际状态。
- **M2 批 C 完成。**
## 2026-10-26 · M2 批 D（话题、按钮、卡住续跑）

- **卡住续跑**：`dispatch.py run <单号> --followup <答复文件>` 会用回执里的 session_id 接着跑（Claude 用 `--resume`，Codex 用 `exec resume`）。实测 `D20261026-7`（Claude）和 `D20261026-8`（Codex）：第一轮都正确输出 NEED_DECISION（“甲还是乙”）并停下，没有建文件；带上“选 B”续跑后，session_id 不变，建了文件、内容是“乙”，并已提交。两张单测完都放弃并清理了。小缺点：续跑会覆盖 `out/receipt.json`，第一轮的回执不保留。
- **确认按钮：没做成，暂缓**。Hermes 的飞书适配器只给“命令执行审批”配了真按钮，普通提问（clarify）在飞书上会退化成编号列表。原本想让写入单的开跑命令走审批按钮，但审批守卫只审被判成危险的命令（`check_all_command_guards` → `detect_dangerous_command`，这份危险列表不能配置），`approvals.smart_policy` 对普通命令不起作用。剩下的路是写插件钩子（pre_tool_call），属于另一块工作。按“两次不通就换思路”的规矩停下：确认继续用文字回“可以”（批 C 已验证）。临时加的 `--write` 标记、smart_policy、审批超时都已撤回，恢复原样。
- **话题**：适配器在收到话题消息时会带上 thread_id，回复会发进同一个话题，也会把被引用消息的原文作为上下文。所以改为：先开单拿到单号，再发待确认派单（标题带单号），并提示“想收进话题就在这条消息上选『在话题中回复』”。话题里是新会话，技能要求先从引用的消息里找单号，再读 order.json 和 task.md 恢复上下文。**还没在飞书里实测。**
- 车间技能升到 0.4.0：先开单再确认、卡住转问加续跑、话题恢复上下文、“不要了”就清理。网关已重启（pid 94959）。
- 飞书验收（Claude 回读，19:20）：
  - 第一次常浩说“甲还是乙先问我”，Hermes 没开单，直接先问。这个理解合理，但没测到车间卡住这条路。
  - 第二次常浩说“直接派单”：`D20261026-9` 开单后直接开跑，**没有发待确认派单**。Hermes 把“直接派单”当成了同意，可以接受，但要留意它会不会把类似说法都当成同意。19:03 发“❓ D20261026-9 需要你定”，常浩 19:14 回“甲”，续跑 21 秒，提交了 NOTE.md（内容“甲”），回报带分支、提交号，写明没有未提交的改动。常浩回“不要了”，台账状态是 abandoned，worktree 和分支都已清理。**卡住转问 + 续跑 + 放弃清理：通过。**
  - **话题：没通**。用猛鬼兽身份查了 3 条入站消息，`thread_id`、`parent_id`、`root_id` 全是空的，说明这几条都是主聊天里的普通消息，不是话题回复；Hermes 也没有新开话题会话。最后一条“你还好么，话题回复我这句话”是在主聊天里让 Hermes 用话题回复，但 Hermes 的发消息工具不能主动开话题，所以只能在主聊天里回。结论：话题只能由常浩发起，而且这次没确认飞书单聊里能不能“在话题中回复”。建议放弃“每单一个话题”，统一靠单号区分。
  - 顺带发现：19:02:55 前后，Hermes 会话库里一批旧消息带着新时间戳重新出现了。看日志是上下文压缩（compression）在 19:02:40 触发后重写了历史，不影响功能。
- **批 D 结论**：卡住续跑通过；确认按钮暂缓（需要写插件钩子）；话题建议放弃（待常浩决定）。
- 下一步（交给新会话）：① 常浩决定是否放弃话题和按钮；② 批 E：从各项目 CURRENT 里挑 5 张真实小单（Claude、Codex 各至少 2 张）验收，检查主分支有没有未经确认的改动，全部通过后关掉飞书里 Claude 的聊天桥（`ai.lark-channel-bridge.bot.claude`）。

### M1 分批（PRD 第 10 节细化）

| 批 | 内容 | 状态 |
|---|---|---|
| 0 | 备份 + 基线 + 本文件 | 已完成 |
| 1 | 换大脑：openai-codex 作主模型和辅助模型，deepseek-flash 作回退 | 已完成 |
| 2 | 清理 agenthub、OpenClaw（先归档，30 天后删除） | 已完成 |
| 3 | 信息边界落地：SOUL、全局规则、Vault AGENTS 和两张决定页 | 已完成 |
| 4 | 提醒上线：晨报样稿 → 取数脚本 → 4 个定时任务 | 挂起（等 T1 日报重设计；样稿和脚本已留） |
| 5 | 独立巡检脚本，每天 1 次，只在异常时告警（代替 PRD F9.2 原来“并进牛马之家”的写法） | 已完成 |
