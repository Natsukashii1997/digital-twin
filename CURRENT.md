# 数字分身 · 当前状态

当前阶段目标：① 常浩本人派活的最短路径打通，dt-sandbox 跑通 5 单（批 2）；② 未闭环账本上线，量出追问基线（丁线 5a）。（2026-11-20 常浩确认，digital-twin#266；取代 11-08 的「个人项目日报 V2 上线 + DT-ORC M5 夜跑」，前者已上线、后者已并入执行底座主线）

需求以 [PRD.md](PRD.md) 为准（v1.1，2026-10-26 已拍板）。本文件只记进度和唯一的下一步，新的记录写在最上面。

证据目录：`~/Library/Application Support/DigitalTwin/evidence/<批次目录>/`（备份含密钥，权限 700/600，不进仓库）。


## 2026-11-20 · #273 未闭环账本 5a-2 全来源（叠在 5a-1 的 PR #272 上，未安装）

- 丁线第二批。`hermes/scripts/openloop.py` 加两个来源：Claude 桌面会话（原样调用 my-skills 拍板清单脚本的 `scan()`、`sift()`）、驿窗送审（只读驿窗的 `handoffs.json`）；同一件事并成一行（明文写的 PR 号正好指到一个开着的 PR 才并，状态取最需要人的，其余记在 `links`）；规格第 6 节的「停了」规则除中台那条都有了。
- 真实环境验收（11-20 18:22–18:35，账本写在 `evidence/2026-11-20-openloop-5a2/`，没动正式目录）：① 在 dt-sandbox 人为开了一个没人审的 PR（#24，验完已关）：收成「待验证」，下一步「送审或请人审」，关掉后出账本。② 会话停在〔等你决定〕：人为造的没造成（由别的会话派来的会话不许自己起新会话），用当天自然停下的真实会话核对，35 个都在。③ 回信没送回会话：人为造的没做（要弄坏驿窗的送达），用当天自然发生的一起核对（postpane#442 的 L3 回信 06:16 到、留了一小时没被会话接走）：在账本里，卡在哪和下一步都对。当时共 101 行。
- 驿窗改 `handoffs.json` 的结构时账本要跟着改；要不要在驿窗那边登记读者由常浩定。dt-sandbox 不在名册的活跃项目里，账本默认不收，验收用的是临时名册。
- **下一步**：常浩定合并 #272 和本 PR（先 #272）；之后是 5a-3（接日报、午晚两张单、安装），要常浩先定卡片样子和定时任务。

## 2026-11-20 · #269 未闭环账本 5a-1 骨架（PR #272，未安装）

- 丁线第一批。新增只读脚本 `hermes/scripts/openloop.py`：名册活跃仓库开着的 PR（现查）和各项目 CURRENT「主线」节各项各占一行，五种状态、到点没动静标「停了」；CURRENT 写着待合并而 PR 已合并或已关的单列。规格 `docs/未闭环账本-规格.md` 第 12 节 5a-1。
- 真实环境验收（11-20 17:51，账本写在 `evidence/2026-11-20-openloop-5a1/`，没动正式目录）：digital-twin 当时开着的 12 个 PR 全在账本里；全部项目扫出 25 处「对不上」，含本文件 #246、#244 两节。共 41 行。
- 没装、没建定时任务、没往飞书发东西、不调 AI；`hermes/install_hold.txt` 已登记先不装。
- **下一步**：5a-2（桌面会话、驿窗送审、并行、全部「停了」规则）叠在本 PR 上做；两个 PR 由常浩定合并；之后 5a-3 要常浩先定卡片样子和定时任务。

## 2026-11-20 · 路线调整：框架对照、批 5 拆分、未闭环账本规格（#266，PR 待常浩合并）

常浩 11-20 与 ChatGPT 梳理了框架《数字分身：从自动化工具集到自主推进系统》，要求对照现状看施工路径对不对。对照结论和调整提案在 `docs/路线调整提案-2026-11-20.md`；常浩同日回「第一组都按推荐」「⑥ 改，⑦ 改，⑧ 改，⑨ 改，⑩ 晚点，优先完成⑧」（逐条登记在 `docs/DECIDED.md` 最上面两条）。

- **已改**：本文件顶部「当前阶段目标」（⑦）；执行底座主线里批 5 拆成 5a / 5b、0D 加第 7 条验收（⑧⑨）；`docs/agent-autoloop-底座-方案.md` 第 9 节同步；PR #211 留言说明第 7 条不挡它合并。
- **PRD v1.4**（⑥）：修订稿六处常浩 11-20 回「六处都改」，已写进 `PRD.md`：目标 5（第一核心要求）、终局七层、F11 工作台、F12 未闭环账本、v2 指标、v2 建设阶段、D27。
- **新增**：`docs/工作台数据约定.md`（五页 76 行逐块对真源，23 行没有真源）、`docs/引擎自有后可解开的限制.md`（14 条）、`docs/未闭环账本-规格.md`（批 5a 规格与分批，只读，不调 AI）。取数零件的实况已核：合并官的 PR 快照 11-10 后没更新，账本自己现查；主线标记没有现成解析；午晚两张单目前没有生成方。
- **没做**：5a 的实现（常浩 11-20 已确认规格第 13 节四条，5a-1、5a-2 另开丁线会话做）；工作台首页加「异常与待推进」（⑩，常浩说晚点）。只改了文档，没装任何东西，全量测试未跑，看 CI。
- **下一步**：常浩合并本 PR；丁线会话做 5a-1、5a-2。

## #253 DT-ORC 6.4「送达未确认」对齐驿窗 postpane#466：没送成的不算（2026-11-20，Claude；PR 待常浩定合并）

- 来由：驿窗 postpane#466（PR Natsukashii1997/postpane#468，已合并）把「没送成」从「送达未确认」里分了出来。常浩 2026-11-20 对驿窗改动确认「送达未确认两处」回「都改」，确认里写明四处规则文字同步改；本仓库是其中一处。
- 改了什么：`docs/DT-ORC/ARCHITECTURE.md` 第 6.4 节驿窗回信对应表第 1 行补一句：插件明确回报没点过发送、或发出时插件没连上（话没出驿窗）的不算送达未确认，按没送成处理：自动往返不停，同一段话直接重送，不出审核结论。点了发送没见开始、说不清点没点的仍是第 1 行原来的处理。只是表格里一句措辞对齐，架构版本号没动（和 #185 对齐 MA-02 时的做法一样）。
- 姊妹 PR：multi-agent#15（MA-02 第 4 节）、my-skills#121（thinking-review 1.1.3）。
- 测试：`/usr/bin/python3 -B -m unittest discover -s hermes/scripts/tests`：`Ran 1608 tests in 6784.431s`，`OK`；`… -s orca/tests`：`Ran 48 tests in 39.343s`，`OK`（基于 `1ba4f84` 加本改动；hermes 那套跑了近两小时，本机同时有多组同样的全量在跑）。
- 证据：`evidence/2026-11-20-253-not-sent/`（`hermes-full.txt`、`orca-full.txt`、`exit.txt`）。

## #251 工程自检报的 7 项安装副本不一致已收口（2026-11-20，Claude；PR #252 已合并 `cb9443b`，已安装）

- 来由：11-20 代验 11-14 那期日报，工程自检报安装副本不一致 7 项；常浩看逐项方案回「按推荐办」，对 7b 回「7b不改」，对旧版副本选「甲：钉住旧版」，PR 回「合并」。
- **装了**：prompts/README.md、ai_access.py、dispatch.py（审计高危修复）从 origin/main `1ba4f84` 装好（装前 main 全量 hermes 1608 + orca 48 通过）；合并后 `ops/self_check.py`、groupdigest-act、workshop 两个技能（含 references）从 `cb9443b` 装好，逐个 `cmp` 一致，14:26 重启猛鬼兽网关、飞书重连。旧副本备份 `evidence/251-drift/install-before/`。
- **收回仓库**：groupdigest-act 第 6 节「常浩质疑报告内容」和 `references/report-content-audit.md`；workshop 三处（登记上游仓库前先查三件事、车间栏照 `D new` 实际值填、换车间先 `D clean` 再重开）。**7b 丢弃**：workshop 第 3 条「前一条回复说过派给谁、他同意就算点名」那句，用仓库版覆盖后已不在安装副本里。
- **登记先不装**：twin_state.py、custody.py 写进 `hermes/install_hold.txt` 并钉住当前安装的旧版（`@<blob 号>`）；**自检行为变化**：写了 @ 的行，安装副本是钉住那版或 main 版都不报，第三种照报（新测试覆盖）。顺手补登记 #225 漏的 secretary_patrol.py。
- **验证**：本分支全量 hermes 1609 + orca 48 通过、CI 通过。干净环境自检用 origin/main 克隆当替身跑：原 7 项和 secretary_patrol 全部不报（替身通过，`evidence/251-drift/selfcheck-after-standin.txt`）。
- **技能放开（PR #254 已合并 `a30fc99`，已安装）**：常浩 11-20「全部放开，hermes自己改了就算」——猛鬼兽、亚古兽可以自己改技能，以安装副本为准；工程自检不再比对技能（脚本、提示词照比）；Claude / Codex 改技能前先收回安装副本的自改；DECIDED 已登记。亚古兽 14:16 自改的两份已原样收回仓库。装了 `ops/self_check.py`、thinking-mainline / evidence-check 两份 SKILL.md（装前核对亚古兽没再改），16:32 重启亚古兽网关，16:33:41 飞书重连。全量 hermes 1610 + orca 48 通过、CI 通过。替身自检（origin/main 克隆）**无输出**（`evidence/skills-selfedit/selfcheck-after-standin.txt`）；下面第 ② 处随之不再是问题。
- **没达到「自检无输出」，两处**：① 主目录 main 拉不齐——主目录有别的会话没提交的 `projects.json`（新增「Hermes 本体（本地补丁）」名册条目），与 main 新提交冲突，`pull --ff-only` 失败，按约定没 stash；自检读主目录本地 main（还在 `d350369`），真实默认跑出 25 项（`selfcheck-after-real.txt`），要那处改动提交或另行处理后才对得上。② 亚古兽 11-20 14:16 自改了 thinking-mainline/SKILL.md 和 evidence-check/references/product-readiness.md（AGENTS 写明两份不许亚古兽自己改），收不收待常浩定。

## #249 常驻任务先等环境就绪（复盘 P9，2026-11-20，Claude；PR #250 已合并 3c6d7a9，已安装）

- 来由：项目坑 L-03（唤醒后代理没通，一周 4 次，每次只修一个脚本）；常浩 2026-11-20 批复盘提案 P9「按改过的」：体检只读 plist 键名，不读进程环境（G-42）。
- 改了什么：新增 `hermes/scripts/netwait.py`（开跑闸门 gate / guard、重试预算 Budget、网络错误判断 is_net_error）；`report_data`、`pdaily_data`、`memdaily_data` 原来各自的等待重试删掉、改调它；8 个在跑的无模型定时任务入口和暂停中的合并官、夜跑主控开跑前先过闸门，等不到明确失败写原因（写稿类记进产物、由发送那次报）；`DT_MODEL_WAIT_SECONDS`、`DT_GH_WAIT_SECONDS` 合成 `DT_NET_WAIT_SECONDS`。`ops/health_check.py` 加网关 plist 代理变量名检查（G-19）和定时任务晚点 / 失败附 pmset 睡眠记录（G-20）；`ops/self_check.py` 把 netwait.py、report_data.py 的亚古兽副本也纳入比对。
- 姊妹 PR：agent-kit#102（G-19、G-20 升〔体检〕）、my-skills#119（automation-checkup 两项）。
- 合并与安装（常浩 11-20 回「合」）：#250 squash 合并 `3c6d7a9`，agent-kit#102 `8eefcfd`、my-skills#119 `e190bdd`（这两个合并前已变基到最新 main 并重跑测试）。按 `self_check.install_pairs` 装了 21 处：猛鬼兽 `~/.hermes/scripts/` 16 个（含新的 netwait.py、self_check.py），亚古兽 `~/.hermes/profiles/agumon/scripts/` 4 个（memdaily_data、memdaily_job、netwait、report_data），`DigitalTwin/bin/health_check.py`；装前核对旧副本都等于改动前 main，备份在 `evidence/p9-env-ready/install-before/`，装后逐字节回读 21 处一致，两处 scripts 目录导入冒烟通过。都是脚本，没重启网关。agent-kit 用 `tools/install.mjs --apply` 同步了运行副本 LESSONS（设置文件无变化）；automation-checkup 的技能链接指向 my-skills 主目录，已拉齐生效。
- 未做：主目录 `digital-twin` 没拉齐：里面有别人没提交的 `projects.json`，和要拉下来的改动冲突，`git pull --ff-only` 被拒；按规矩没 stash、没 reset，等常浩处理。真实环境下的第一次闸门运行是明早 07:30 记忆日报、09:15 日报写稿，09:45 巡检第一次带两项新检查。
- 测试：hermes 全量 `Ran 1607 tests`，`FAILED (failures=117, errors=3)`：117 条是 `test_secretary_gate` / `test_secretary_patrol`，origin/main `241b22b` 上单独跑同样 117 条失败（「本机没有 yaml 库」，本改动之前就有）；3 条是 memdaily 重试测试被隔离默认的「不等网络」关掉了重试，已修，复跑 `test_memdaily`、`test_netwait` 49 条 OK。orca 48 条 OK。
- 证据：`evidence/p9-env-ready/`（全量日志、复跑日志、真机只读核对 `claude-06-真机体检两项只读.txt`）。

## 安全审计 PR #171 再次续跑核验（2026-11-18，Codex，常浩授权继续原简报；计费阻断）

- 时间：核验记录 `2026-11-18 10:38`。
- 本轮核对：四仓库已交付分支和原始全量结果均有效，复用既有通过结果。收尾跟进 main 的单个 CURRENT 提交 `24e3dde24b32c5265dd8974e02dfcae83060deb2`，变基前后 hermes/orca 完整 tree 哈希相同，证据 `resume-attempt-3-rebase.json`。
- 测试：本地仍为 Hermes 1158/1158、ORCA 48/48；同一代码树的原样日志见下方。CI 运行 44221659322 已实际重试为 attempt 3，test job 在 2 秒内结束、steps 为空，GitHub 再次返回账户付款失败或支出上限；原样回执 `resume-attempt-3.json`。
- 交付：仍只更新 codex/audit-fix-1112 和 PR #171；结论仍有问题，当前需账户所有者处理 GitHub Billing & plans 后复跑 CI。
- 本轮新坑：无。

## 安全审计 PR #171 二次刷新（2026-11-18，Codex，常浩授权刷新审计分支；待合并）

- 时间：开工 `2026-11-18 09:41`（由 Git reflog 的 09:41:40 变基记录回读核对）。
- 改了哪些文件：本节；`hermes/scripts/tests/test_autoloop_grants.py` 为既有 CLI 测试统一夹具时钟，并新增 CLI 到期拒绝回归，断言退出码和准确 JSON。生产凭据校验保持当前实现。
- 测试：最新 main 首次全量 `Ran 1157 tests in 633.495s` / `FAILED (failures=1)`，唯一失败是 CLI 测试签发时钟固定而检查使用真实时钟。修复后完整全量原样 `Ran 1158 tests in 746.262s` / `OK` / `EXIT_CODE=0`；ORCA `Ran 48 tests in 12.652s` / `OK` / `EXIT_CODE=0`。模块 40/40，临时绕过到期判定会被新回归抓到。
- 验证：收尾 fetch 发现 main 在第一轮后又前进 15 提交，重新变基到 `73a826c37c68701fbd1d3d6d8df760d4bbb51fdd`，三提交重放无新增冲突。下方第一轮 1127 + 48 的记录保留为历史结果，最终采用本节最新基线与计数。证据 `~/Library/Application Support/DigitalTwin/evidence/2026-11-17-refresh/`，最新全量 `hermes-latest-final.log` / `orca-latest-final.log`，首败与反证均保留。
- 收尾同步：main 又新增 2 个仅改 CURRENT.md 的提交，已变基到 `ed442d84c41f313f4404c3d7f97284090704b777`。变基前后 hermes 与 orca 完整 Git tree 哈希相同，因此上述全量覆盖最终代码与测试；哈希证明见证据 `validated-code-trees.json`。
- 发布与回读：更新指定审计分支与 PR #171，运行副本采用另行验收。
- 遗留问题：本地全量通过，但 GitHub Actions 运行 `44221187547` 的 test job 没有启动，steps 为空，annotation 明确为账户付款失败或支出上限需要提高。结论改为仍有问题，需常浩在 GitHub Billing & plans 处理后重跑当前提交 CI。回执在证据 `ci-billing-block.json`。ResourceWarning 原文保留。
- 本轮新坑：无。

## 安全审计 PR #171 刷新（2026-11-17，Codex，常浩授权变基、验证、推送；待合并）

- 时间：开工 `2026-11-17 18:00`。
- 改了哪些文件：本轮新增本节交接；保留原审计 dispatch.py 和 test_dispatch_github.py 的修复与回归，无缺失测试需补写。
- 测试：`python3 -B -m unittest discover -s hermes/scripts/tests` 原样 `Ran 1127 tests in 2178.881s` / `OK` / `EXIT_CODE=0`；ORCA `Ran 48 tests in 22.751s` / `OK` / `EXIT_CODE=0`。当前 Python 3.14，原样 ResourceWarning 保留。main dispatch 对照 20 项中 8 败，修复实现 20 项全过。
- 验证：变基至最新 main `3478dcd`。两个原提交都只在 CURRENT 冲突，main 最新主线与状态完整保留，审计及返修记录追加保留。代码无文本冲突，现行隔离和授权逻辑逐项核对。证据 `~/Library/Application Support/DigitalTwin/evidence/2026-11-17-refresh/`。
- 发布与回读：本轮更新指定审计分支和 PR #171，目标运行副本尚未采用。
- 遗留问题：仓库没有 docs/LESSONS.md，已如实记录；没有合并阻断项。项目整体仍按下方新执行底座主线推进。
- 本轮新坑：无。

## 主线：数字分身执行底座改为 fork agent-autoloop（#188）｜完成 4/11

常浩 2026-11-15 定：只有基于 agent-autoloop 框架的这一条主线；常浩会 fork 它自己开发；数字分身、驿窗等已有探索里有价值的 agent 自动化内容融合进来；新结构跑通后只保留这一套。同日先说托管暂停，后改为**秘书代批「选 B」：托管从一开始并入新框架**（猛鬼兽按常浩签的清单代批，工人仍关在 dt-worker 里）。方案 V2.4：`docs/agent-autoloop-底座-方案.md`（PR #189 已合并）；思维评审两轮已收口（决策记录 `docs/reviews/2026-11-15-执行底座改用agent-autoloop-思维评审.md`）。本机已装上游 v1.8.5（`~/.agent-autoloop`，launchd `agent-autoloop`，代理启动脚本在 `~/.agent-autoloop/bin/`），只有试跑规则。

- ✅ 方案：V2.4，Fable 5.2 独立评估 + ChatGPT High 两轮评审，常浩 11-15 回「代批选 B，9 件事都按推荐」；PR #189 已合并 `832d7ca`（同时把 CI 固定到 ubuntu-24.04：ubuntu-latest 已是 26.04、没有 Python 3.9），#188 已关
- ✅ D 档不代批：常浩 11-15 回「D档还是都不能替我批」，维持授权清单 v1
- ✅ 批 0A 隔离门：PR #191 已合并 `928f26b`、已安装；dt-worker 越权反向测试 22 项全挡住，`claude -p` 正常；过滤代理、门卫规则在生效；merge_act 不再在主账号跑 PR 代码
- ▶ 批 0B 授权门（#195）：秘书代批规格 v0.4 定稿，PR #196 已合并 `5b9d2d3`（思维评审两轮成熟退出；常浩 11-17 定：任何时候可代批、白天先等 30 分钟；清单 v1 开提问卡 / 重派 / 退回 / 合并；21:00 汇总；在途点击「获准送达前本人优先」；dt-sandbox 加最小 CI 做合并验收）；常浩 11-17「按计划开工」，0B 分 6 批（0B-1 任务凭证 → 0B-2 中台配置与只读窗口 → 0B-3 飞书按钮实测 → 0B-4 代批核心 → 0B-5 推翻与汇报；0B-6 dt-sandbox 加最小 CI 可并行），完整验收等 0C 后一起。✅ 0B-1 任务凭证与派单网关：PR #199 已合并 `cd90a4e`（L3 两轮 + 常浩授权专项续审，通过；未安装，hermes 来源密钥 0B-2 建）。✅ 0B-2 中台配置与只读窗口：PR #200 已合并 `9f3f3ca`（常浩 11-17「按计划开工」即合并；L3 四轮，第 4 轮通过；`b381b89` CI 通过；PR 落后 main 的提交只改 CURRENT.md、不重叠，未变基直接锁定版本合并，合并结果除 CURRENT.md 外与审过的版本一致）；**已安装**（常浩 11-17 回「装」）：在 main `4ad0176` 上跑 install 和 issue-source-key（--confirm --target ~/.agent-autoloop）；装前 rules.json、config.json、hub.db 备份在 evidence/autoloop-config/install-20261117-213605/；status 回读：hermes 来源和钥匙在、feishu / secretary 通道在、两条派单规则已载入且暂停（原因「0C 启动脚本就绪前暂停」，由常浩决定恢复）、钥匙文件 DigitalTwin/autoloop/hermes-source.key 权限 600；plan 显示没有要改的；装前装后中台 runs 2 条、events 3 条不变，没有派单跑起来。▶ 0B-3 飞书按钮实测：常浩 11-17「按计划开工」（三小步：探针 PR → 装插件、重启网关、发测试卡、常浩点一次 → 白话结论）；读代码推断：点击人 open_id 网关有（HERMES_SESSION_USER_ID，插件原来没取），原消息号网关不转（HERMES_SESSION_MESSAGE_ID 是回调 token），靠按钮里的随机令牌对卡；第 1 小步 PR #201 已合并 `ba7d38a`（常浩 11-17「不送审，合并」；探针只记录不触发动作；全量 1152 + 48 通过，CI 通过）；**插件已安装**（常浩 11-17「三件都做」；旧版备份 evidence/button-probe/merge-buttons-init.before-20261117.py；新版同时带上 #133 托管按钮转交，custody_report 未安装、没有托管卡，点不到）；21:59 重启网关，飞书重连正常；以猛鬼兽机器人发了两张测试卡（登记卡尾号 f81e，没登记的卡尾号 d4a7，都回读核对过），button_probe 从仓库 main 直接跑、不装。**实测结论（22:00）**：谁点的——插件拿得到常浩 open_id（HERMES_SESSION_USER_ID，和 owner 文件对上）；在哪点的——chat_id 对上私聊，但 chat_type 私聊也报 group，只能认 chat_id；原消息号——插件里拿不到（HERMES_SESSION_MESSAGE_ID 为空），只能靠按钮里登记过的令牌对卡，登记卡对上、没登记的卡被认出；重复点击——f81e 第二下没到网关（原因不明），去重要在脚本层做（令牌一次性）；插件分不出点按钮和打字 /card（推断）。结论与原始记录在 evidence/button-probe/2026-11-17-结论.md。✅ 0B-3 实测完成。▶ 0B-4 代批核心：常浩 11-17「按计划开工」（4a 本人飞书批 → 4b 清单与账本 → 4c 秘书巡视代答①②③ → 4d 代合并④，每批一个 PR，按推荐都送 L3）；4a PR #202（分支 claude/195-feishu-bridge，`ba12769`：autoloop_feishu 飞书桥接、autoloop_answer 本人点击核对与本人在途、插件转交、autoloop_config issue-channel-key；全量 1170 + 48 通过，临时中台端到端，改坏 24/24；顺手修 test_autoloop_grants 时间炸弹——main 上 11-17 22:00 起这项测试会失败），L3 第 1 轮「返修」（4 个 P1、4 个 P2），返修 `9ba6c4e`：送达前点击只排队、确认后提交（真中台验证送达前答会被 4xx 拒）、错误分类（明确拒绝才释放、5xx 和超时记结果不明）、claims 状态机和 reconcile 对账（从不重答）、秘书契约（本人不改秘书行、owner_holds 给 4c）、改卡核消息号、别处点击不回话、签钥匙先占文件；全量 1181 + 48 通过、改坏 38/38；P1-01（同一 macOS 账号的进程能伪造点击参数、也能直接读通道钥匙）常浩 11-17 **选甲**：4a 合并但不签 feishu 通道钥匙、不装，隔离在 ADR-015 定（方向：独立账号 dt-gate + 单独飞书机器人直接收点击，0C 前定）；`4b656b2` 第 2 轮「返修」（新 P1-05 重复发送资格、P1-06 送达确认没落盘、P1-07 过期对账覆盖新尝试，P1-04 的 60 秒放开条件不成立），返修 `39c2b29`：尝试号 + 唯一发送资格、对账按登记原样重报送达（真中台验证幂等）、条件更新、结果不明不自动放开（要常浩明确同意 release --confirm）；全量 1187 + 48 通过、改坏 45 处抓到 43 处（剩下 2 处是同一事务里互相兜底的两道检查，一起改坏能抓到）；L3 两轮已用完，常浩 11-18「授权第 3 轮」，第 3 轮「返修」：第 2 轮 4 个 P1 全部关闭，新 P1-08（中台卡片状态认不出时当成「开着」，可能误放开或误结算），返修 `20390e8`：只认 open / decided / done / closed，认不出一律当结果不明；P2-08 旧库结构拒用；全量 1192 + 48 通过、改坏 6/6；常浩 11-18「授权专项复核」，复核**通过**（P1-08 关闭，P2-07、P2-08 关闭；P2-09 旧库先查后建已顺手改 `747bb45`，全量 1192 + 48 通过）；`20390e8`、`747bb45` 的 CI 因 GitHub 账单没启动；之后 PR 和 main 冲突（test_autoloop_grants 时间炸弹两边都修了），合并 main 取 main 写法 `3313bd1`，全量 1192 + 48 通过，**CI 在 `3313bd1` 通过**；常浩 11-18 回「合并」，✅ **PR #202 已合并 `096851e`**（压缩合并、锁定 `3313bd1`）；▶ 4b 秘书开关、代批清单与账本：常浩 11-18「按推荐开工」（复用选甲：和托管共用底座；签了清单不算开，私聊「开秘书」才开），PR #207（`d8dc69a`：secretary.py、secretary_ledger.py、twin-state v5、docs/代批清单-模板.md；custody / custody_ledger 抽出用途参数，托管行为不变；全量 1249 + 48 通过，改坏 61/61），L3 第 1 轮「返修」（4 个 P1：签署没固定项目→仓库映射、交常浩被名册挡、决定没记清单版本、原文可空；P2 口令处理不改行为只改说明），返修 `79f5e3f`、合并 main `3ef32f0`，全量 1334 + 48 通过、CI 通过、改坏 23/23；第 2 轮「返修」（第 1 轮 4 个 P1 全关；新 P1-05：名册 inventory 不合格仓库名能签进状态、签完读不回），返修 `4801514`（inventory 同样校验、落盘前结构复核）；常浩 11-18「授权专项复核」，复核**通过**（P1-05 关闭，无新增问题；驿窗 30 分钟超时停了接力，回信是浏览器只读核到的）；本机全量 1336 + 48 通过；`4801514` CI 通过；常浩 11-18 回「合并」，✅ **PR #207 已合并 `ef7cae0`**（压缩合并、锁定 `4801514`）；不签清单、不装、不开秘书；正式 twin-state 库首次被已安装脚本打开会自动升 v5，安装前单独问常浩；▶ 4c 秘书巡视代答：常浩 11-19「按推荐开工」（分两小批：先控制面、再秘书侧；③退回 PR 用机器账号留言，凭据 0E-4 接）；4c-1 控制面 PR #218（`7324ca8`：secretary_gate.py 前后闸、获准、送达、对账；twin-state v6 同卡同版本唯一；顺手修 autoloop_publish.hub_card 按整数查卡片的已有 bug；全量 1381 + 48 通过、改坏 56/56），L3 共 6 轮（第 3–6 轮常浩 11-19 授权专项续审）：第 1、2 轮关闭 ① 运行证据、REAL_ASK 关闭（常浩选甲，不破零补丁，中台按版本核对是验收 12）、②③ 全程持受信发布锁、送达证据按版本；第 3 轮剩 5 项常浩回「按推荐」：① 卡住超过 2 分钟核不出一律对账不清、同任务冻结（回到第 2 轮前的保守口径），按钮带版本号（**没带版本的旧样式按钮一律拒**），桥接单写锁，schema 严格整数；第 4 轮补：中台查不到就不发、补报送达和直接发卡都拿锁、改消息前核版本、秘书对账缺字段判说不清；第 5 轮常浩选甲：中台每递一次卡算一次尝试、9 次后永久放弃（given_up，ask 卡无恢复入口），桥接改为领卡前先查中台库、读不到不领并私聊提醒，领到后才读不到报 failed 写明原因，已放弃的卡每张私聊说一次，报完送达放锁再提交本人回答；第 6 轮 P1 关闭、剩 2 个 P2，常浩选甲「修完不再审」：本地版本比中台新报 failed、报 failed 出错也提醒（`37d7ebe`、`94dee5c`，PR 上已写「补改未复审」）；全量 1486 + 48 通过、改坏 144/144、测试前后正式文件哈希一致、CI 通过；回信摘录在 evidence/reviews/2026-11-19-PR218-L3-*；✅ **PR #218 已合并 `3211b13`**（常浩 11-19 经驿窗回「合并」；压缩合并、锁定 `94dee5c`；合并时 `94dee5c` 的 CI 还在排队，上一提交 `37d7ebe` CI 通过，`94dee5c` 只多一条测试、本机通过；合并后 hermes/ 与审过的版本逐字一致）；不签钥匙、不装、不开秘书，REAL_ASK 关闭；▶ 4c 第 2 小批（秘书侧）：常浩 11-19「按推荐开工」（①送达开关关着时巡视不调模型），PR #225（分支 claude/195-secretary-patrol，`31e7198` + 合 main `76d8bb9`：新增 secretary_patrol.py；控制面两处改变已有行为——①开关关着一进前闸就交常浩、弃权出错时 choice 可为 null；全量 1503 + 48 通过、改坏 24/24、正式文件哈希一致）；L3 共 5 轮（链 r1a1f33b25e300，回信摘录 evidence/reviews/2026-11-19-PR225-L3-第{1,2,3}轮、2026-11-20-PR225-L3-第{4,5}轮），**第 5 轮通过**（`462514f`，未决 P0 0、P1 0；P1-05 同账号隔离转 #230，P2-01 常浩选甲：开关关着也先查来路再交）：①判断进程的运行证据另核 --ignore-rules 管不到的叠加——按 YAML 语义（safe_load、utf-8-sig，重复键 / 复杂键 / 构造异常核不清）读 config.yaml，没有自定义系统提示、人格、预载消息、外部密钥源，两种读法主模型一致；Hermes 读的环境文件（.env、.op.env、安装目录 .env，安装目录要有 hermes_cli/main.py）去 BOM、去 NUL 后出现 HERMES_ 就交常浩；有管理员级配置目录（/etc/hermes、$HERMES_MANAGED_DIR）就交常浩；交常浩写卡片号必须带版本；CI 装 PyYAML 6.0.1（和 /usr/bin/python3 对齐）；审后补第 5 轮非阻断 P2-07（HERMES_MANAGED_DIR 不是绝对路径核不清，`cba84e9`，未复审）；3.9 全量 1587 + 48 通过、改坏 76/76 + P2-07 相关 6/6、正式文件哈希一致（3.9 一遍 hub.db 只多了中台自己的心跳）；`cba84e9` CI #445 通过；✅ **PR #225 已合并 `11e3bf1`**（常浩 11-20 经驿窗回「合并」；压缩合并、锁定 `cba84e9`；合并后 hermes/、CI 配置、ai_access.json 与 `cba84e9` 逐字一致）；不签钥匙、不装、不开秘书，REAL_ASK 关闭；▶ 下一项 4d 代合并④、再 0B-5 推翻与汇报，各先给常浩白话计划；向上游要的中台扩展点：answer 按版本核对（验收 12）、given_up 卡恢复入口；桥接正式启用前 run 要受监督常驻（单跑 once 不扫已放弃的卡）；③现有审查记录绑不了 PR 和受审提交，一律交常浩，来源批 4 再定；不签钥匙、不装；P2（handoff 只认可回读的中台事项）放 4c；不签、不装、不开；合并后不签钥匙、不装；DigitalTwin/owner_chat_id 已于 11-20 配好（日报按钮要它，见日报改版主线）；未签 feishu 通道钥匙、未装。✅ 0B-6 dt-sandbox 最小 CI：dt-sandbox#22 已合并 `016a957`（11-18；247 项测试通过；故意弄坏的草稿 #23 变红、已关闭）
- ✅ ADR-015（底座方向、信任边界、迁移回退）：PR #206 已合并 `170dc54`（常浩 11-18「合并」）。ChatGPT High 思维评审 1 轮「决策升级」；核实猛鬼兽、亚古兽的飞书用户令牌都能以常浩身份发消息；常浩 11-18「都按推荐」：dt-gate 控制面 + 不带 AI 的门卫机器人、本人批准只认门卫点击、代批生效前秘书必须有效降权、无人值守全权 AI 进程纳入检查；0C 不等 0E，受信发布按归 dt-gate 写、不装真实凭据。修订 1（11-18「都按推荐，原则6改」）：秘书降权走 4b，猛鬼兽、亚古兽一起搬进 dt-secretary；PRD 原则 1、6 改字面
- ▶ 批 0E 控制面搬家与秘书降权：常浩 11-18「按计划开工」，分 0E-0 文档 → 0E-1 两个账号初始化与门卫规则 → 0E-2 中台和网关搬进 dt-gate → 0E-3 门卫机器人 → 0E-4 受信发布与合并执行器（等 0C）→ 0E-5 猛鬼兽、亚古兽搬进 dt-secretary → 0E-6 三类反向验收；0E-1 起按 L3 送审。✅ 0E-0 文档（ADR-015 修订 1、PRD v1.3、方案 V2.5）PR #208 已合并 `642ff84`。常浩亲手：建 dt-gate、dt-secretary 两个标准账号；在飞书开放平台建「门卫」应用（只开以应用身份发消息，可用范围只选常浩，回调配置等 0E-3），凭据不经任何 AI
- ✅ 批 0C 执行门（#205）：PR #209 已合并 `d4a8557`（常浩 11-18「合并209」）；L3 四轮（第 3、4 轮常浩授权专项复核），第 4 轮「通过」；未安装、未在 dt-worker 部署，派单规则仍暂停；REAL_PUBLISH 继续关闭。详见下面「0C PR #209 已合并（未安装）」
- ⏳ 0D 恢复门（PR #211 在审）。常浩 11-20 加第 7 条验收：工人被强杀、断网、超时后，这张任务出现在未闭环账本里、带状态和下一步；等丁线 5a 有了再验，不挡 #211 按原 6 条合并（#266）
- ⏳ 批 1 飞书拍板通道
- ⏳ 批 2 派活（dt-sandbox 5 单）
- ⏳ 批 3 出口（受信发布、合并、发飞书、改定时规则）
- ⏳ 批 4 审查交付
- ▶ 批 5a 未闭环账本（丁线，发现与报告，只读）：常浩 11-20 定批 5 拆两半、5a 现在做（#266）。规格 `docs/未闭环账本-规格.md`；5a-1 骨架 PR #272（#269）、5a-2 全来源（#273，叠在 #272 上）都已开 PR、在审，都没装；5a-3 起要常浩先定
- ⏳ 批 5b 续接与恢复（批 2 之后，用任务凭证和出口账本）；夜间提案位置不变
- 📌 批 6 驿窗只显示（先做改动确认）
- ⏳ 批 7 新增自动化；批 8 收口退役、ARCHITECTURE V6

## 旧主线：托管上线（#126）｜完成 3/8（常浩 2026-11-15 定：并入新框架，按「秘书代批」重做，下面留作资产记录）

上线标准见 #126：沙盒夜跑里最小闭环端到端跑通 + 一次托管合并 + 三道闸各一次拦截实证，之后常浩签托管策略才生效。

- ✅ #132 合并隔离：机器账号 + main「限制更新」，12 项反向测试通过；PR #146 `0a70219`（L3 三轮，第 3 轮通过），merge_act 已安装
- ✅ #131 托管状态底座 custody.py：PR #167 `85611f7`（L3 三轮，第 3 轮常浩授权，通过）；已安装（托管仍不生效）
- ▶ #135 执行防线反向测试：PR #169 `7cf6613` 已合并，结论不验收（容器经代理能访问宿主本机服务、经局域网 IP 能连宿主端口）；#174 身份闸 PR #175 已合并安装（`351d444`）；#172 出网 PR #173 第 2 轮 L3 11-14 09:34 结论「需修改」（0 P0、4 P1：agent-up.sh 可绕开、前置检查失败不清理已在跑的执行器、backend 仍能用 Squid 出口、回退时旧执行器有启动窗口；驿窗没转回会话，11-15 才发现，原文 `evidence/135-defense/fix-egress-r2/review-r2-reply.md`），11-15 已交 `local_2699c8aa` 会话返修，之后送第 3 轮（要常浩授权）；本机装的是第 2 轮送审版；两份都合并后由常浩决定 #135 是否重新验收
- ✅ #133 账本、额度、汇报卡、推翻：PR #186 `90fd20a` 已合并（L3 两轮 + 定向复核，P1-01 最后一处补改未复审，常浩 11-14 说「合并」）；未安装，安装副本 4 个文件落后于 main（见下）；汇报卡样式常浩 11-14 选乙版（表格）；11-14 常浩说「试发」，造数据的乙版卡已发私聊并回读（`om_x100b60b47fe1b8a0c1b546339b7e6d9`）
- ⏸ #136 托管合并：常浩 11-18 定「停在这里」，PR #187 保持打开、不合并，留作资产（`custody_merge.py`、策略第 3 节签署时校验、一致率排除 owner=custody；全量测试通过，L3 第 1 轮需修改：3 P0 + 4 P1 + 2 P2，没返修）。审查教训给新框架批 0B 代批 / 批 3 合并出口的规格：① PR 文件列表要分页取全并和 changedFiles 核数（`gh pr view --json files` 超过 100 个会悄悄截断），改名的旧路径也要查；② 来源归属不能靠工人自报的 PR 地址，要用主控自己记的号（Multica 时代分支名末 12 位 = run 号末 12 位）；③ 授权有效性要同时看停机、结束、截止时间、冻结；④ 门禁之后、动手之前再复核 HEAD、base、CI；⑤ 结果不明不能按时间判「没做成」，只有服务端明确拒绝或确认没发出才释放额度；⑥ 防重复要跨托管会话按 PR × HEAD 查；⑦ 审核凭证要核来源（谁写的），不只核格式。原文在 PR #187 的 CURRENT 分节和驿窗接力 `h1a1dd4f22e200`
- ✖ #134 已停，并入 #188；资产在 claude/134-custody-night 77616a0
- ⏳ 沙盒端到端最小闭环
- ⏳ 常浩签托管策略（D 档，只能常浩）

#135 网络缺口修好并重新验收之前，**托管保持禁用，也不开夜跑**（隔离执行器能访问宿主本机服务）。

---

## 主线：猛鬼兽日报改版（#226）｜完成 7/7

常浩 2026-11-19 反馈日报「形式大于使用价值，没有行动指引」；当天在私聊三版真卡片预览里选乙版，对实施计划回「都按推荐」，第 1 批合并时说「合并并安装，继续直到完成任务」。定稿登记在 [docs/DECIDED.md](docs/DECIDED.md)，规格在 [docs/个人项目日报-V2-设计.md](docs/个人项目日报-V2-设计.md) 4.0，PRD D22。

- ✅ 结构方案、三版预览选乙、实施计划都按推荐
- ✅ 第 1 批版式：PR #229 已合并 `c589327`，已安装（pdaily_publish.py、pdaily_report.md、HERMES.md，旧版备份 evidence/2026-11-19-pr229-install/），网关已重启；验证卡已发私聊、回读通过
- ✅ 第 2 批拍板按钮和回复闭环（#235）：PR #241 已合并 `3d2f790`（独立审查三轮，第三轮「可以合并」；CI 通过，锁定 `9de2fcd`）
- ✅ 第 3 批猛鬼兽查只读单、自检按计划不装的不报（#236）：PR #243 已合并 `a867612`（合 main 解决 7 处冲突，均为第 2 批压缩合并所致；全量 1524 条在 `df40eb2` 上通过，合并后相关 4 组 172 条通过；CI 通过，锁定 `647b600`）
- ✅ 安装（2026-11-19 23:02）：pdaily_publish.py、pdaily_act.py、self_check.py → ~/.hermes/scripts/；pdaily_report.md → ~/.hermes/prompts/；HERMES.md → ~/.hermes/workspace/；pdaily-act 技能 → ~/.hermes/skills/autonomous-ai-agents/pdaily-act/；merge-buttons 插件 → ~/.hermes/plugins/merge-buttons/。旧版备份和全量测试日志在 evidence/2026-11-19-pdaily-b23-install/；装后逐个 cmp 与 main 一致；网关已重启，飞书 23:03:42 重连成功（第一次连接超时，重试连上）。工程自检不再报按计划不装的 13 个脚本，输出存 evidence/2026-11-19-pdaily-b23-install/self_check.out。下一次 10:00 日报起带按钮，风险表可能出现「猛鬼兽派只读单查，明天报」——**未在真卡片上验证**
- ✅ Hermes 配置 `plugins.entries.merge-buttons.allow_gateway_injection: true`：常浩 11-19 回「开」，用 `hermes config set` 写入（回读 true），23:07 重启网关，飞书 23:09:10 连上；点「按推荐」后由插件注入私聊会话交猛鬼兽办。按钮的具体样子等常浩在 11-20 的真卡片上看过再登记进 DECIDED（📌，不挡主线）。11-20 代验发现按钮点了会被拒：本机只配了常浩 open_id、没配私聊号（buttons.log 11-19 21:23 那条），收尾时漏查；常浩 11-20 回「写入」，用他 11-19 17:38 的私聊消息跑 `twin_identity.py setup-chat --write`（飞书回读确认 p2p），写进 DigitalTwin/owner_chat_id（600），和日报发送用的私聊号一致；用 pdaily_act 同一套代码只读核对：身份两项都在、最新卡（统计日 11-19）令牌对得上、①②④ 放按钮、③ 要常浩亲手做不放；没有真点按钮，点击后记账、注入、改卡片未在真实环境验证
- ✅ 车间 Claude 只读单改成真正只读：另一会话 PR #242 已合并 `4f79eaa`，run_worker.py 安装副本和 main 一致；第 3 批的只读单仍写死 Codex（要不要放开 Claude 另议）

工程自检剩 9 项安装副本和 main 不一致，都不是这两批碰过的文件：prompts/README.md、ai_access.py、custody.py、twin_state.py、groupdigest-act、workshop 技能（早于本批的漂移），亚古兽的 sidebar_reports.py 和 sales-sidebar 两份（主目录有另一会话没提交的改动）。要不要补装另行确认（custody.py、twin_state.py 补装会改变运行中的行为）。

---

## 2026-11-20 · #99 M5 小修按 Codex 审查返修并变基（PR #102 待常浩合并，无需安装）

常浩 11-20 清 PR 积压时回「按建议」：#102 改完 11-11 Codex 审查的 3 项再合。Claude 接手分支，变基到最新 main。

- ① 版本检查：新加 `hermes/scripts/tests/test_python_version.py`，不是 3.9 时在测试输出最后提示一句、给出合并前验证用的命令；**不判失败**。Codex 建议判失败，没照做：测试 README 写着有的模块 3.9 和 3.14 都要过，判失败会让所有用 `python3` 跑的会话都红。只加在 hermes 这套，`orca/tests` 没加。
- ② 名册查重：`test_roster.py` 先展开 `~` 并规整路径再比（和 dispatch、merge_act 的用法一致），`~/x` 和绝对路径、末尾斜杠、`./`、`../` 都算同一个目录；补 2 条用例。撤掉规整，新用例失败。
- ③ 执行单 `docs/DT-ORC/migration/2026-11-08-M5-首夜执行单.md` 第二节补一句证据文件带写入方前缀和序号，指向 AGENTS。
- 变基：`hermes/scripts/tests/README.md`「预期」一段两边都改过，取本 PR 的（不写死总数）；本 PR 的两节 11-08 交接挪回 11-08 的位置；两个新测试文件第一句补 `import _isolation`。
- 行为变化（都是 #99 原本要的）：名册里 digital-twin 的测试命令从 `python3` 改成 `/usr/bin/python3`，合并官和派单变基后跑的是它；非 3.9 跑测试多一行提示。
- 测试：见 PR 评论（本机 3.9 相关模块 + CI 全量）。
- **下一步**：常浩说「合并」后合并；#99 随之关闭。


## 2026-11-19 · #246 工作台分身页（PR 待常浩合并）

常浩 11-19 回「按这个逻辑出」，三版（控制台 / 授权表 / 闸门路径）里选丙（逐条内容见 `docs/DECIDED.md` 最上面一节）。

- 分身页回答「分身能替我做到哪一步」，只讲开关和规则：一张卡片从上往下走 8 道闸（秘书开着吗 → 清单签了吗 → 属于四类吗 → 项目在清单里吗 → 碰到九条了吗 → 额度够吗 → 先等多久 → 秘书选编号），每道显示当前设置，不过就交给常浩；右栏是现在的状态（带「关秘书」）、授权四档、在猛鬼兽私聊说的口令。示例数据和首页、侧栏一致：秘书关，代批清单 v1 已填未签，额度用模板推荐值、已用 0。
- 验证：真实浏览器 1440×900 和 1280 宽看过，侧栏点「分身」能打开，控制台无报错、无横向溢出；首页、汇报、主线、协同四页和改前逐像素对比无差异。基线截图在 `evidence/2026-11-19-workbench-twin/`，合并脚本在同目录 `build-final/`。只做了电脑版。只改了设计稿和文档，全量测试未跑，看 CI。
- 没对上的：汇报页示例里 11-18 夜间秘书替常浩定过 4 条，和分身页「清单未签」矛盾，汇报页示例要不要改等常浩定（改已定内容要单独确认）；dt-sandbox 还没有 CI（0B-6 才加），④合并实际合不了，页面上标了「dt-sandbox 还没 CI」。
- **下一步**：常浩合并 PR；之后设计执行页（AI 接入四块加工人账号 dt-worker），先拟逻辑等确认。

## 2026-11-19 · #244 亚古兽「思考主线」技能、核验技能收窄（PR 待常浩合并，已安装）

- **起因**：常浩 12:27 说亚古兽分析项目纪要「本末倒置」。查到三处：五个思考模型在 SOUL 里只有五句定义、没有步骤也查不出用没用；亚古兽自建的 `business-analysis-coaching` 触发说明太宽（已加载 27 次）、正文是七步执行顺序，成了分析的主流程；后台自动复盘把常浩的纠正一次次写成这个技能的补丁（共 12 次）。
- **做了什么**：新增 `hermes/skills/thinking-mainline/`（六步：问、铺、抓、挖和破、立、落；第 3 步之后才列核验清单；回答里要有目标、主要矛盾及排除理由、根因、取舍）；`business-analysis-coaching` 收窄成 `hermes/skills/evidence-check/`（两份参考文件带过来，只改 product-readiness 第 7 节一段）；两者登记进 `ops/self_check.py` 的 `AGUMON_SKILLS`。六步和五个模型的对应关系是 Claude 推断的，常浩还没逐条改过。
- **试跑**：临时 profile 用同一份纪要、同一模型（gpt-6.2-sol）跑了对照组 1 次、新设置 2 次。第一轮漏了目标和根因，据此把这两样改成必须单独成句；第二轮首句、目标、根因（标推断）、取舍都有。对照组比中午那版好很多，中午的问题部分来自 6.1 和长会话。每版只跑一次，不能说稳定。记录在 `evidence/agumon-thinking-trial/`。
- **已安装**（常浩说「装」）：两份技能装到 `~/.hermes/profiles/agumon/skills/productivity/`；旧技能用 `hermes -p agumon curator archive` 归档到 `skills/.archive/`（可 restore）；SOUL.md「思维内核」用法段和 workspace/HERMES.md 第 2、3 节各加一条（源文件改动在 niuma-home 的 PR）；23:09 重启 `ai.hermes.gateway-agumon`，已连上飞书；改前备份在 `evidence/agumon-thinking-install-20261119/`。常浩要在飞书发 `/new` 才用上新人设（G-26）。
- **没做 / 待定**：没关亚古兽的自动技能复盘（`skills.creation_nudge_interval`）；`curator pin` 只防删除不防修改，靠每天自检比对发现被改。全量测试两次都卡住没出结果（同时段另外两个工作区的全量测试也卡着），只确认 `test_self_check` 39 条通过。
- **下一步**：常浩合并本 PR 和 niuma-home 的 PR；用几天后看亚古兽的分析是否稳定按主线写。
## 2026-11-19 · 车间 Claude 只读单改成真正只读（#236 后续；PR #242 已合并 `4f79eaa`，已安装）

- 缺口（#236 第 3 批独立审查发现，本次实测确认）：`run_worker.py` 的 Claude 只读单只加了 `--allowedTools`，本机 bypassPermissions 下照样有 Bash、Write、Edit 和 42 个 MCP 服务，Write、Bash、desktop-commander 三种方式都写成了文件。
- 改成 `--tools Read,Grep,Glob --strict-mcp-config --permission-mode dontAsk --allowedTools Read,Grep,Glob`；写入单、Codex 不变。代价：Claude 只读单跑不了任何命令（git、gh 也不行），Codex 只读沙箱里还能跑。
- 测试：新增 test_run_worker_readonly.py 5 项（改前代码上失败 2 项）；真 claude 反向验证脚本 hermes/scripts/tests/claude_readonly_probe.py（手动跑，不进全量）；全量 1500 项通过，CI 通过。常浩经驿窗回「合并」「装上」（仓库不允许自动合并，CI 通过后锁定 `32115d9` 合并）。
- **已安装**：`~/.hermes/scripts/run_worker.py` 换成 main 版本（装前和 main 的上一版逐字一致，备份 evidence/readonly-claude-236/install/run_worker.py.before-4f79eaa）；装后用安装副本实测：工具只剩 Read、Grep、Glob，没有 MCP，dontAsk，写不出文件；真跑一张只读单，退出码 0、读文件正常。证据 evidence/readonly-claude-236/。
- 没做：日报「猛鬼兽查」（pdaily_act cmd_checks）仍写死 Codex，放不放开跟随车间默认等常浩定；考虑到上面的代价，核对风险这类活继续用 Codex 可能更合适。

## 2026-11-19 · #239 工作台主线页（PR #240 已合并 `494a330`）

常浩 11-19 回「按这个逻辑出」，三版里选乙（原话和逐条内容见 `docs/DECIDED.md` 最上面一节）。

- 主线页是地铁线：页头一条「等你」；每条主线一条线、一项一站，点一站看它的任务单（按等你 / 审查中 / 在做 / 没开始 / 已合并分列）和猛鬼兽巡视的话；已并入的主线折成一行。示例数据按本文件的两条主线改写。
- 验证：真实浏览器 1440×900 和 1280 宽看过，点站切换正常，控制台无报错；首页、汇报、协同三页和改前逐像素对比无差异。基线截图在 `evidence/2026-11-19-workbench-main/`，合并脚本在同目录 `build-final/`。只做了电脑版。只改了设计稿和文档，全量测试未跑，看 CI。
- 分身页已接着做，见 #246。

## 2026-11-19 · #237 工作台协同页（PR #238 已合并 `b756f1f`）

常浩 11-19 三轮选稿定（原话和逐条内容见 `docs/DECIDED.md` 最上面一节）。

- 协同页只讲规则：工作流是泳道，每一步的规则（做什么、用什么、怎样算过、不过怎么办）接在泳道正下方同一列；拖动、加步、删步后自动检查交接，改动先送「等我拍板」。自动化按负责人分组登记 21 个（猛鬼兽 8、亚古兽 3、Codex 5、Claude Code 2、本机 3），只有是什么、做什么、什么时候跑、在跑 / 已暂停。页面里不放说明文字。
- 验证：真实浏览器 1440×900 和 1280 宽看过，用鼠标拖块换顺序、拖模块加步、删步、撤销、切页签都正常，控制台无报错；首页、汇报页和改前逐像素对比，汇报页一致，首页只差浮动动画。基线截图在 `evidence/2026-11-19-workbench-collab/`，合并脚本在同目录 `build-final/`。只做了电脑版。只改了设计稿和文档，全量测试未跑，看 CI。
- 没动：首页写的「9 个自动化」、侧栏「协同 6 · 9」（另行确认要不要改成 21）。
- 已合并；主线页见上面 #239。

## 2026-11-19 · #226 个人项目日报改乙版第 1 批（PR #229 已开，未合并、未安装）

- 改了 `hermes/scripts/pdaily_publish.py`（卡片要素、核对、乙版卡片、事实版、核对不过重写一次）、`hermes/prompts/pdaily_report.md`、测试、样例 `hermes/samples/pdaily-card-b-2026-11-18.json`，同步设计文档、工作规程第 5 节、PRD D22、AGENTS、DECIDED。
- 测试：hermes 1495 项全过（约 30 分钟）、orca 全过。用 11-18 的事实快照让 Hermes 按新提示词实跑写稿（只出稿不发）：8 件拍板、7 类风险，核对通过。过程文件在 `evidence/pdaily-redesign/`。
- 实跑发现：按编号硬拦「同一件事在风险和拍板两处说」会误判（各仓库 #7 重号，整份被降成事实版），改成只记提醒。
- 待：常浩说合并 → 装 `pdaily_publish.py`、`pdaily_report.md`、`HERMES.md` 到 `~/.hermes/`（HERMES.md 改了要重启网关）→ 发真卡片验。

## 2026-11-19 · #232 工作台汇报页重做（PR #233 已合并 `19cc35e`；状态标签样式修复 PR #234 已合并 `5531937`）

常浩 11-19 三轮选稿定（原话和逐条内容见 `docs/DECIDED.md` 最上面一节）。

- 定稿文件改名为 `docs/design/workbench.html`（以后不随版本改名），汇报页换成：报告页签 → 六个数字 → 一夜时间轴 → 等我拍板 → 昨晚的经过（一件一件的白话，句子里标出秘书的决定和做完的动作，右栏是要你做）→ 额度条。
- 示例数据：等我拍板加了来自夜间的两张，共 6 条，首页清单行和刊头数字跟着变。
- 验证：真实浏览器看过首页和汇报页，点「去合并」跳到拍板区，控制台无报错；基线截图 3 张在 `evidence/2026-11-19-workbench-report/`。只做了电脑版。只改了设计稿和文档，全量测试未跑，看 CI。
- 实现时要补：白话由猛鬼兽写（新的 AI 功能，要登记 AI 接入）；额度读数的记录点。
- 合并后发现：汇报页的状态标签用了类名 `.st`，和首页工作流行里的状态块（`.wfr .nm .st`）撞名，首页每条工作流左下的状态块多了一圈边框。已把汇报页的类名改成 `.stt`，首页恢复原样。合并后重截了首页整页基线 `evidence/2026-11-19-workbench-report/workbench-home-fullpage-1440.png`（带问题的旧图改名为 `-before-234.png`）。
- 协同页见上面 #237。

## 2026-11-19 · #227 工作台第 4 版：侧栏四组八个入口、首页拍板清单行、汇报页（PR #228 已合并 `ab31a89`）

常浩 11-19 在会话里逐条定（原话见 `docs/DECIDED.md` 第 4 版一节）。

- 定稿文件 `docs/design/workbench-v4.html`（由第 3 版改名并修改）：侧栏改成首页、汇报｜主线、协同｜执行、分身｜记录、沉淀；首页的等我拍板改清单行，原来的黑底四卡搬到汇报页，下面接交班报告；改动类主按钮「确认生效」改为「确认」。
- 登记表：新增第 4 版一节，第 3 版的第 1、4、6 条标为已修订。
- 验证：真实浏览器 1440×900 看过首页和汇报页，点「确认」、切到汇报、控制台无报错；基线截图 3 张在 `evidence/2026-11-19-workbench-v4/`。只做了电脑版。只改了设计稿和文档，没有脚本改动，全量测试未跑，看 CI。
- **下一步**：见上一节 #232。

## 2026-11-19 · #216 每周提案卡片新增来源「再犯与待提升」（PR #217 已合并 `f17f5e6`，已安装）

常浩 11-18 定（跨项目复盘 P1）；11-19 回「按推荐开始」，合并。依赖 agent-kit#77、#81 已于 11-18 合并，agent-kit#84（「关闭」的坑不进升级首选）11-19 合并并重装。

- 变基到 `6120638` 后临时 HOME 全量：hermes 1390 项 1 项报错、orca 48 项全过；报错的是 `test_autoloop_feishu` 的 `test_delivered_but_crashed_before_recording_recovers_once`（清临时目录时 hub worker 还在写 `runs/2-1`，Directory not empty）。只跑这个模块不加载本 PR 的文件，PR 分支 3 次错 1 次、main 5 次全过，判为偶发、与本 PR 无关。CI `test` 通过。证据 `evidence/2026-11-19-pr217-rebase2/`。
- 已装：`proposal_digest.py` → `~/.hermes/scripts/`，`SKILL.md` → `~/.hermes/skills/autonomous-ai-agents/proposal-digest/`，`cmp` 一致；旧版备份在 `evidence/2026-11-19-pr217-install/`。`launchctl kickstart -k gui/$(id -u)/ai.hermes.gateway` 重启，13:00:27 飞书重新连上（pid 36084）。
- 定时任务：~~`81464e7ea4e3` 没改~~ 更正（11-20）：`81464e7ea4e3` 已于 11-11 按猛鬼兽清单删除（见下文「已删除」一行），照原写法 11-23 不会发。11-20 常浩对改动确认回「恢复独立任务」，按旧任务参数重新注册为 `4fece1545136`（`0 10 * * 1`，script `proposal_digest_draft.py`、技能 proposal-digest，结果投递 local、失败投递私聊；`hermes cron list` 读回一致；任务表前后与本地预览在 `evidence/2026-11-20-proposal-digest-restore/`）。待：11-23（周一）10:00 第一期带「再犯与待提升」（本地 `send --dry-run` 预览已含，替身通过）；`DigitalTwin/shadow/guards-decisions.md` 要等常浩回复卡片、写回结论时才建出。

## 2026-11-19 · 亚古兽接手销售 AI 侧边栏固定汇报（PR #219 已合并 `6a29c36`，已安装、已建定时任务）

常浩 11-19 定：销售侧边栏是公司项目，由亚古兽负责；三项固定产出——需求反馈每天 18:00 先私发确认、功能同步通知他说了才出、项目推进进度每周三 18:30 私发；其他项目问题在飞书问亚古兽。11-19 回「按推荐做，确保生成的卡片跟确认版本一致，私发我确认」。

- 版式固定在 `hermes/scripts/sidebar_reports.py`，亚古兽只写内容 JSON；回归基准是定稿原件（11-18 进度卡片 `card_final.json`、11-17/11-18 两期需求反馈发出稿），逐字节一致。做法打包成亚古兽技能 `hermes/skills/sales-sidebar/`（常浩 11-19「打包成 skill」；两个定时任务都挂它），项目档案在亚古兽工作目录 `销售侧边栏-项目档案.md`，规程第 9 节在 `~/.hermes/profiles/agumon/workspace/HERMES.md`（不在仓库，改前备份在 DigitalTwin/backups/）。
- 已安装到 `~/.hermes/profiles/agumon/`；定时任务 `da4fac30ca49`（每天 18:00）、`4e1ea12562e2`（周三 18:30）；首期存档迁入 `DigitalTwin/sidebar/`（11-18 两份定稿）。
- 试跑：需求反馈 11-19 11:02 群里无新消息，回一句「本期不出」；重做 11-18 只出稿，和定稿对比内容一致，发现原话换行会断开引用块，已修；进度卡片按 11-12～11-19 试发私聊 `om_x100b60d2d30680acc055909758bcb0b`，按试跑问题补了反馈闭环口径、下个节点来源、自然周、卡片不放口径说明。
- 常浩 11-19 回「没问题」（试发卡片）、「打包成 skill」、「Merge」；合并时 main 未设必需检查，`--auto` 当场合并，CI 随后 success。ops/self_check.py 新版已装到 ~/.hermes/scripts/（旧版备份在 DigitalTwin/backups/）。
- 待：今晚 18:00 第一次正式跑；11-25 第一次周报。
## 2026-11-18 · #212 测试统一隔离与反向验证核对工具（PR 待审，未合并）

- **为什么**：11-17 那次误装的修法只隔离了 `test_autoloop_config.py` 一个模块；`hermes/scripts/tests/` 44 个测试模块没有统一隔离（同类还有 10-27 G-13、11-11 `8e3890a`）。常浩已批准做这一批。
- **统一隔离** `hermes/scripts/tests/_isolation/`：每个测试模块第一句 `import _isolation`（47 个都加了，含 #209 新进来的 3 个）；按仓库测试命令跑时这个包排在所有 `test_*.py` 前面先导入，漏写的模块也在隔离里。导入时清掉 `DT_*`、`HERMES_*`、`FEISHU_*` 等变量，`HOME`、`XDG_*`、`AGENT_AUTOLOOP_HOME`、`HERMES_HOME`、`CODEX_HOME`、`CLAUDE_CONFIG_DIR`、`DT_GRANTS_STATUS`、`DT_GRANTS_TASKS`、`DT_AUTOLOOP_SHARED`、`DT_PUBLISH_OUTBOX` 指到临时目录，`DT_HUB` 指到 127.0.0.1:9；本进程禁网（回环放行，本机正式服务端口照拦），子进程拿死代理；`PATH` 最前面放 `launchctl`、`security`、`lark-cli`、`hermes`、`claude`、`codex`、`gh` 等拦截桩。`test_isolation_guard.py` 扫描目录，第一句不是它就失败（夹具 `fixtures/isolation_bypass/` 证明会判失败）。`test_autoloop_config.py` 里 #200 的环境变量隔离并入统一机制，只留名册、命令行、中台请求三个桩。
- **反向验证工具** `hermes/scripts/tests/prod_snapshot.py`：只取路径、类型、大小、修改时间，不打开文件；默认核对 `~/.agent-autoloop`、`~/.hermes`、`~/Library/LaunchAgents`、`DigitalTwin`、`/Users/Shared/dt-autoloop`，变了退出码 3 并列出路径；用法写在 `hermes/scripts/tests/README.md`。#200 和之后各批的改坏脚本在证据目录里（不在仓库），没改；以后的改坏验证用它。
- **验证**：变基到 `e047ccb` 后 `/usr/bin/python3`（3.9）全量 1288 + orca 48 通过、0 跳过（变基前 3.14 全量 1214 通过），都在外层临时 `HOME` 保护下跑；最终那次用 `prod_snapshot.py run` 包住，前后摘要按排除规则 0 处变化（排除的是 Hermes 网关和中台自己一直在写的心跳、缓存、cron、日志、`hub.db-wal`，空闲对照里也会变）。证据 `evidence/212-test-isolation/`。顺带发现：`test_autoloop_hub_e2e` 用的真 `agent-autoloop` 命令行会调 `launchctl print gui/501`，现在被桩拦下，测试照常通过。`test_exec_gate_e2e`（#209）原来用 `~/.agent-autoloop` 找正式中台和上游源码，隔离后会指到空目录、`UpstreamParser` 2 项会被悄悄跳过，改成按系统账号取（`_isolation.REAL_HOME`），它的前后核对也从读文件内容算哈希改成只看大小和修改时间（`config.json` 里有钥匙）。
- **下一步**：常浩安排审查（路径规则判 L3）后决定合并；开着的 PR 新加的测试文件（#207、#210、#211、#187、#102）合并到这之后要各加第一句 `import _isolation`，否则守护测试失败；#211 的 `prod_guard.py` 宜改用本机制（它的核对会读 `master.key`、`secrets.env` 内容算哈希）。

---

## 2026-11-18 · #205 批 0C 执行门：0C PR #209 已合并（未安装）

- **做了什么**：工人侧启动脚本 `ops/dt_worktree.py`（挂在中台 Agent dt-claude / dt-codex 的 program 上：每轮按凭证镜像和任务单准入，单号固定工作目录、分支 `<执行方>/<单号>`，每单累计 2 小时，取消和超时整组停进程、不交接，CLI 退出后提交并打交接包）；受信发布最小版 `hermes/scripts/autoloop_publish.py`（按 ADR-015 归 dt-gate：交接包只当资料，按凭证库核动作和代次，交接包要落到中台里当前代次、有正面派单证据的一轮，逐个提交查高风险路径，只推 `<执行方>/<单号>`、不 force）；autoloop_config 给两个 Agent 挂 program；dt_worker_setup.sh 建工作和交接目录。dt-sandbox 规则集放行 `claude/**`、`codex/**`（常浩 11-18 定，基线同步）。
- **审查**：L3 四轮。第 1 轮 7 个 P1、5 个 P2；第 2 轮剩 4 处；第 3 轮（常浩「0C按甲」）关 2 处；第 4 轮（常浩「按甲，授权第4轮」）「通过」。非阻断意见：临界区措辞已改；「测试后端没强制 gh 是假的」不改（只有受信测试代码能注入，以后换测试专用 PR 接口）。
- **接受的剩余风险**：0C 证明不了交接包确实由所标轮次生成（存心作恶的工人能在当前代次重做旧内容，或把旧包挂到正常结束、只是没改动的轮次上；这种轮次不补「停下了」，否则会盖掉 CLI 提的问题卡片，常浩 11-18 定）。兜底：凭证、PR 审查、常浩合并；0D 用独立可信账本记「轮次 × 交接头提交」。
- **验证**：全量 1265 + 48 通过；故意改坏 54/54（`evidence/0c-exec-gate/mutate-r3.py`）；临时中台端到端通过；每轮测试前后核对正式中台没变。常浩说「合并209」后带 `--match-head-commit b75333c` squash 合并 `d4a8557`。
- **REAL_PUBLISH 继续关闭**：0D 之前受信发布整体不干活（推分支、开 PR 都不做）。要等 0D 的发布串行化和结果账本、0E 的 dt-gate 真实账号验收都做完，由常浩决定什么时候打开；审查通过不等于可以打开。
- **没装的**：启动脚本没装进 `/Users/Shared/dt-autoloop/bin/`，dt-worker 没重跑安装脚本，中台 Agent 没改（要常浩说「装」），两条派单规则仍暂停（只能常浩决定恢复）。每一步都要常浩单独同意。
- **身份闸基线**：合并后 main 上的基线已含 `claude/**`、`codex/**`，和 GitHub 一致，夜跑身份闸不再因此拒绝。
- **下一步**：0D 恢复门另开会话；0C 的安装和 dt-worker 部署等常浩决定。

---

## 2026-11-17 · 事故：0B-2 反向验证误装到本机正式中台，已恢复

- **经过**：18:22 跑 PR #200 的「故意改坏」时，「装配置不加锁」那一项让 `test_install_and_key_share_one_lock` 直接跑了 install。这个单测只把锁文件指到了临时目录，中台目录、名册、钥匙文件都没隔离，于是对 `~/.agent-autoloop` 生效：备份原文件 → 写入数字分身那一块 → 两条派单规则载入并暂停。18:26 「签钥匙不加锁」那一项同样签出了正式的 hermes 来源钥匙，写进 `DigitalTwin/autoloop/hermes-source.key`。
- **影响**：派单规则一直暂停，没有派单轮次跑过；hermes 来源收到 0 个事件；派单网关没安装，没人用过这把钥匙。但这是常浩没批准的常驻配置改动。
- **恢复**（20:5x，按 P-14「改坏了先撤回再报告」）：`agent-autoloop key remove source hermes`（钥匙即时失效），删掉钥匙文件；`rules.json` 换回误装时自动留的备份 `evidence/autoloop-config/rules-before-20261117-182252.json`（和 11-15 的 bak 相比只多了后来加的 program 两行，就是误装前的原样）；误装后的版本另存为 `rules-accidental-install-20261117-182252.json`。回读确认：rules.json 和备份逐字节一致，中台只剩「试一下」规则和 desktop 通道，钥匙清单没有 hermes。中台日志里留着对两条派单规则名的暂停记录（规则已不在，不起作用；以后装上时派单规则直接是暂停状态），没动。
- **根因修复**（在 PR #200 里）：`test_autoloop_config.py` 整个模块先把中台目录、钥匙文件、备份、锁、令牌、名册都指到临时目录，命令行换成 `/usr/bin/false`，网络请求一律抛错；锁测试把真正的安装和签发换成「跑进来就报错」的桩。改坏脚本每跑一项都核对正式中台（规则文件、钥匙文件、钥匙清单），变了就立刻停下。修完重跑 17 处改坏，正式中台全程未变。
- **待办**：提议把「测试和改坏验证漏隔离会动到本机正式服务」升为全局坑，等常浩批准。

---

## 2026-11-17 · #195 批 0B-1 任务凭证与派单网关（PR #199 已合并 `cd90a4e`，未安装）

- **做了什么**：`hermes/scripts/autoloop_grants.py`。写入派单要两步批准：propose 随机生成 8 位确认码，由脚本自己以猛鬼兽机器人把五要素发到常浩私聊；常浩回「派单 <码>」后，approve 经 twin_identity 核验，签发凭证（执行代次 1）。网关只有两个入口往中台发 hermes 事件：建任务、按有效凭证续接。事件只带单号、代次、类别，五要素写在工人只读的任务单 `/Users/Shared/dt-autoloop/tasks/<单号>.md`。
- **发送**：每次领取事件，都在同一写锁下先按已提交状态写工人镜像，再现读凭证。冻结、撤销先在凭证库生效，再写镜像；镜像写不成就报 `mirror_error`，派发停下。恢复要「恢复 <单号> <冻结码>」，而且要有更正记录（`record_resolution` 由 0B-5 调用，本批恢复实际关闭）。名册只认沙盒和 `write_allowed: true`。
- **审查**：常浩授权 L3。第 1 轮「返修」1 条 P0、4 条 P1、1 条 P2。P0-01（猛鬼兽和网关同一账号、模型有 shell）常浩 11-17 选甲：沙盒阶段先接受，dt-gate 留 ADR-015 决定。第 2 轮「返修」3 条 P1（领取与镜像竞态、冻结失败语义、名册白名单）。审查预算用完后常浩说「继续送审」，专项续审「通过」。
- **非阻断意见**：① 已领取的事件仍可能在冻结后送到中台，旧代次的副作用靠主账号出口按代次拦；② 中台同一来源同一 id 去重（`kernel/decide.ts` ingest），端到端实测放在 0B-2。
- **验证**：39 项单测，16 处「故意改坏」全被抓到（`evidence/autoloop-grants/`）；全量 1114 + 48 项通过，CI 通过。常浩说「合并」后带 `--match-head-commit 6dd3c9b` squash 合并。
- **小问题**：驿窗第 3 次送审（专项续审）的回信没转回会话，常浩截图告知结论。
- **下一步**：0B-2 中台配置与只读窗口，先出分批计划给常浩看。

---

## 2026-11-17 · 记忆日报汇总文档满 4 万块：按月分文档（方案甲，PR #198 已合并 `64cbcaf`，已安装；11-14～11-16 已补写）

- **事故**：《常浩-记忆日报汇总》`TCG9dy7Uco…` 到了飞书单文档 4 万块上限，11-14、11-16 两篇写入被整篇拒绝（`DOC_TOTAL_BLOCK_LIMIT`，加上这篇分别 40,258、40,138）；11-15 那次 11-16 15:12 才跑，亚古兽退出码 130 被中断（另一个问题，未查）。三篇都没写进去，也没写进去一半（两次写前快照逐字节相同，文档顶部仍是 11-13）。
- **现状数据**：文档 39,331 块（列块接口数，比飞书自己数的少 213）、137 篇；亚古兽写的每篇 800～1,300 块，一个月约 1.6～2 万块，按季度拆也会超。证据 `evidence/memdaily-block-limit-20261117/`。
- **常浩 11-17 选方案甲**：旧文档原样归档、不改名、不再写；从 11-14 起按日期所在月写《常浩-记忆日报汇总 · YYYY-MM》，当月第一篇写入时自动建（常浩的用户身份；默认不指定文件夹，由飞书放到常浩云空间的默认位置，登记文件里填 `folder` 可改）；写前数当前文档块数加本篇估算，超过 3.6 万提前开「续 N」；飞书仍报超限就开续篇再写一次；指定 `--doc` 出样稿时超限直接报中文原因。各份文档登记在 `DigitalTwin/memdaily/docs.json`（没有就只认旧文档）。「已写过」和「前一篇行动 & 阻塞」先读当月文档，空着再往前读到旧文档。新开文档时私聊推送多一行说明。仍是插到最顶部、7/15 永久模板。PRD D20、AGENTS、ai_access.json 已同步。
- **合并与安装**（常浩经 Agent HUD 回「合并并安装」）：CI 通过后带 `--match-head-commit 7030755` squash 合并为 `64cbcaf`，#197 关。`memdaily_data.py`、`memdaily_publish.py` 装到 `~/.hermes/profiles/agumon/scripts/`，旧副本在 `evidence/memdaily-block-limit-20261117/install-before/`；三个 memdaily 脚本与 main `cmp` 一致，`/usr/bin/python3` 导入正常；本机还没有 `docs.json`（第一次写入时建）。无模型定时任务每次现读脚本，不用重启网关。明早 07:30 会写 11-17，那一篇会先建出 2026-11 文档。
- **补写**（常浩「现在补写」，赶在 11-18 07:30 之前，免得 11-17 排到它们下面）：15:48 用安装副本写 11-14（用 11-15 已核对的稿），自动建出《常浩-记忆日报汇总 · 2026-11》`YyyIdP4ABovnwPxAhMQcIsPInPe`（常浩所有，登记在 `DigitalTwin/memdaily/docs.json`）；接着 `run` 11-15、11-16（重新取数写稿，gpt-6.1-sol 一次过核对；11-16 的前一篇接上 11-15，旧 11-16 稿留在 `evidence/memdaily-block-limit-20261117/old-1116/`）。三篇都回读通过、已私聊推送；新文档从上到下 11-16、11-15、11-14；旧文档最后修改时间仍是 11-14 09:48，没被动过。日志在同一证据目录 `backfill-*.log`。
- **待办**：③ 牛马之家日报页只读一个 `report_doc_url`，换文档后只看得到当月，要在 niuma-home 改成读多份（另开会话）；④ 11-15 退出码 130 查因。

---

## 2026-11-17 · #190 批 0A 隔离门（PR #191 已合并 `928f26b`，已安装）

- **工人账号 dt-worker**：标准账号；Node 24、Claude（浏览器登录）、Codex（`--device-auth` 验证码登录，浏览器回调方式卡了两天）已装；用接单密钥 join 中台成功、交接密钥已删。踩坑：git 不信任主账号所有的引擎副本（dubious ownership），安装脚本里给这一个副本加 `safe.directory`。安装脚本源文件 `ops/dt_worker_setup.sh`，共享副本 `/Users/Shared/dt-autoloop/setup-dt-worker.sh`，可重复跑。
- **越权反向测试**（`ops/autoloop_isolation_check.py`）：第一次 22 项挡住 17 项，漏的是主账号家目录可列、能直连 / 经加速器代理绕回主账号的本机服务（Claude 桌面本地接口、数据库 5433、加速器控制口 8085 等）。修完重跑 **22 项全挡住**（`/Users/Shared/dt-autoloop/results/isolation-dt-worker-20261117-142548.json`）；dt-worker 里 `claude -p` 能回 OK（常浩 11-17 确认）。
- **修法**（常浩 11-17「同意装」，D 档）：① 常浩 `chmod 700 /Users/nat`；② 过滤代理 `ops/dt_worker_egress.py`（launchd `cn.digital-twin.egress`，127.0.0.1:8090，只放行 CONNECT 到公网 443，转给加速器 8086；已装）；③ 门卫规则 `ops/dt_worker_pf.sh`（pf 锚点 `com.apple/dt-worker`，只准连 3350、8090；LaunchDaemon `cn.digital-twin.pf-dt-worker` 开机和每 5 分钟重载；常浩已用 sudo 装上）；④ 每日巡检查过滤代理拦不拦本机、门卫状态文件新不新（health_check 已装）。闪电猫只有系统代理、没有虚拟网卡，「全局模式」解决不了绕回。剩余风险：DNS 重绑定且加速器直连时过滤代理挡不住。
- **不变量 2**（常浩 11-15 已同意）：merge_act 去掉「没 CI 在主账号干净副本跑测试」，没 CI 的仓库不走飞书合并；act.repos 七个仓库都是 ci: true，实际不受影响。merge_act、merge_rules.json、actions.py、merge-officer 技能**未安装**，PR 合并后同步并重启网关。ARCHITECTURE 5.5、托管设计 2a 仍写着「没有 CI 的仓库在干净副本跑」，代码现在更严，基线文字留到批 8 ARCHITECTURE V6 一起改。
- **合并与安装**（常浩 11-17 说「合并」）：本机全量测试、CI 通过后 squash 合并 `928f26b`，#190 关。merge_act.py、merge_rules.json、actions.py → `~/.hermes/scripts/`，merge-officer 技能 → `~/.hermes/skills/autonomous-ai-agents/`，已重启猛鬼兽网关、飞书已连上；旧副本备份在 `evidence/190-isolation/install-merge-act-20261117-145241/`。merge_act 安装副本原先落后 #133 的托管撤销函数，这次一并带上（只被未安装的 custody_report 调用，不生效）。自检仍报 #133 的 5 项（custody.py、twin_state.py 不同，custody_impact / ledger / report 缺）没装，和本单无关。
- **下一步**：批 0B 授权门，先出秘书代批规格给常浩看，只送这一块审查。

---

## 2026-11-16 · #192 AI 前沿群每日学习报告（PR #193 已合并 `93e171b`，11-17 已安装、已建定时任务，验收中）

- **常浩 11-16 定**（回「都按推荐」）：猛鬼兽每天 10:45 给私聊发前一天外部群「Codex重置许愿群」的深度学习报告；现在就挂在猛鬼兽定时任务上，在 agent-autoloop 方案第 141 行登记为前台推送、不迁；PRD D1 补「含 AI 前沿学习推送」、新增 D25；每天发，周末精简版。
- **做了什么**：`hermes/scripts/groupdigest.py`（collect / build / send / preview）+ 两个定时任务入口 + 提示词 `hermes/prompts/groupdigest_report.md`。取数以常浩用户身份读群（11-10～11-15 每天 5～44 条），外链源头先直接抓、抓不到再走 agent-reach（它的网页读取走第三方读取服务，11-13 连抓 30 个被限流，改成直接抓优先后 21/30 直接读到）；模型只写材料编号 M/S，脚本填链接，核对编号、网址、报告板块（第二轮起五节）、「已写入」类说法，略超字数照用、超 1.5 倍打回；不过重写一次，再不过出事实版。
- **实测**：11-13 真实材料（26 条、30 个源头）经 `hermes chat` 一次写成并通过核对（Opus 5.5，思考 high，约 6 分钟），完整报告约 3.2 万字。
- **第一轮样式**（常浩 11-16：「直接把版式预览飞书发给我，不要做成 artifact」）：甲 / 乙 / 丙三版发过私聊，常浩选丙，又嫌底部「去做 / 去学」两栏太窄，丙底部三种改法也发过。
- **第二轮：常浩重定结构**（11-16 晚）：卡片分三个板块 + 一段总结。① 昨日信息梳理：每条标题说清是什么事、下面展开说明，**只讲信息、不写对他的价值**；② 总结「这些对你的帮助」；③ 行动清单（确定能动手做的）；④ 思考采纳建议（要他想清楚再定的提案）。顶上一排色块写消息数、要点数、可做数、待你想数，底部「查看完整报告」按钮；要直观、层级清楚、有呼吸感，用好引用、代码块、斜体、表格、色块。
  - 提示词改成这套字段（highlights 的 title / detail / quote / quote_zh / who / read，help，todo，consider），完整报告改五节（信息梳理、对你的帮助、行动清单、思考采纳建议、路径建议）+ 覆盖与可信度。
  - 防编造多一条：原话必须能在它引用的材料里逐字找到，找不到就删掉原话、记提醒（不打回）；命令代码块里的网址也查。
  - 11-13 真实材料重写一次通过核对（Opus 5.5，6 条信息都带原话，2 件可做，3 条待你想）。
  - 三版：一 分区面板 `om_x100b60859cb8e0acc0e98bd6fb65319`、二 表格对照 `om_x100b60859c55fca0de96ee156d32a88`、三 色带分区 `om_x100b60859c7708a0df0788a73cbd471`，都回读通过；在飞书桌面端截图查过两轮，序号角标深字压在深色底上看不清，改白字后重发一版 `om_x100b6085980e88a8c081adf0dee8364` 确认（另两版同一处已改，没重发）。挑定前默认一版（`DT_GD_STYLE`）。
- **没做**：没合并、没安装、没建定时任务（等常浩挑定样式、合并后装：10:00 `groupdigest_build_job.py`、10:45 `groupdigest_send_job.py`）；X 推文的回复和引用推文没抓（agent-reach 的 X 渠道没装，装要另外确认）；「值得永久采纳」的提案常浩回复后怎么落地还没有接。
- **定稿**（常浩 11-16：「用第二版，但是还需要再优化」）：用二 · 表格对照，去掉信息梳理里的原话（只留总结）、去掉「这些对你的帮助」一段，「行动清单」改叫「今日行动清单」，其余不动。一、三两版和样式切换（`DT_GD_STYLE`、`preview --styles`）删掉，`preview` 改成只发一张【预览】卡。常浩接着说「你都删除原话了，昨日信息梳理这里稍微紧凑一点」：每条标题和总结放进同一列、行距 4px，出处接在总结末尾，去掉条间空行。原话和「对你的帮助」仍在完整报告里（报告五节没改）。常浩又说单独一块「第 1 条要用到」很突兀：命令改成放进该条「做什么」格子里的行内代码。定稿预览 `om_x100b6086720364a4df7fbd72a42b47a`，回读通过、飞书截图查过。
- **回复即派单**（常浩 11-16 提出，回「都按推荐」：回复后不再逐单确认、点名 Claude、和卡片同一个 PR）：
  - 提示词：今日行动每条多给 `owner`（claude / you）和 `projects`（名册 id）；核对只认名册 `projects` 里的 id，对不上的项目丢掉，一个都不剩就改成「你」。
  - 卡片：今日行动清单加「谁做 · 在哪」列和条目编号；有 Claude 条目时结尾一句「要我写成任务派给 Claude 吗？回『都派』，或『派 1 3，2 不做』」。
  - 脚本：`groupdigest.py todos`（最近一张发出去的卡片的行动清单和派单情况）、`mark`（记单号或不做，同一条同一项目不派两次）；记账在 `<日期>.dispatch.json`。
  - 新技能 `hermes/skills/groupdigest-act/`：核对卡片 → 整理选择 → 每条每个项目写任务说明 → `dispatch.py new --tool claude` → `mark` → 后台 `run`，回报和合并按 workshop。回复即这几条的 D 档逐项授权（同 D23），不发「待确认派单」；只开到 PR。
  - 11-13 材料按新提示词重写一次：3 条行动里 1 条标 Claude（对照 Managed Agents 四条原则给 agent-autoloop 出差距清单，digital-twin），2 条是你自己做；预览 `om_x100b60863e2778a0c0ca4b2a5795dd1` 回读通过、飞书截图查过。这次发现核对把「没有任何一条已经采纳」当成说成已做，已修（前 8 个字有否定词不算）。
  - 没做：没装、没在真实回复上跑过（要装好后常浩回一次才算验证）。预览卡上的「派」现在回了猛鬼兽接不住。
- **合并**（常浩 11-17 说「合并」）：PR #193 CI 通过（另在本机用 Python 3.9 跑全量 1026 项、orca 都通过），squash 合并为 `93e171b`，主目录拉齐。没装，私聊里的【预览】卡在技能装好前别回「派」。
- **安装**（常浩 11-17 回「安装并建任务」）：`groupdigest.py`、`groupdigest_build_job.py`、`groupdigest_send_job.py` → `~/.hermes/scripts/`，`groupdigest_report.md` → `~/.hermes/prompts/`，技能 `groupdigest-act` → `~/.hermes/skills/autonomous-ai-agents/`，五个都是新文件、SHA-256 与 main 一致（`evidence/192-groupdigest/install-20261117-134825/`）；网关重启后 13:49:08 飞书重连，`hermes skills list` 里 groupdigest-act 已启用。
- **定时任务**（猛鬼兽，无模型）：`6b8dd243009c` 每天 10:00 写稿（结果只记本地）、`19ec55894baf` 每天 10:45 发私聊；首次 11-18（报 11-17）。
- **装后试跑**：13:49 用 `hermes cron run 6b8dd243009c` 手动跑一次写稿（11-16：19 条消息、9 个源头全抓到），13:53 一次通过核对（Opus 5.5；6 条信息、3 条行动其中 2 条标 Claude、3 条思考，只有 3 处略超字数的提醒），没发送；`cron run` 是在命令行进程里跑的，不完全等于网关环境，11-18 10:00 那次才算网关里的实跑。下次运行时间没变。
- **提前发 11-16 那份**（常浩 11-17 回「现在发」，好今天就试「派」）：14:37 以猛鬼兽发到私聊 `om_x100b608b2d931880c303632b8118d53`，回读通过，完整报告云文档已建；飞书桌面端截图查过，四个数字块、信息梳理、今日行动清单（第 2、3 条标 Claude，项目 digital-twin）、思考采纳表、报告按钮都正常，结尾问「回『都派』，或『派 2 3』」。`groupdigest.py todos` 读到这张卡，可派 [2, 3]。
- **卡片调整**（常浩 11-17：「思考采纳建议和今日行动清单换下顺序；最后的问题再显眼一点，我都看不出这是问题，用红色问号」）：分支 `claude/192-card-order`，思考采纳建议挪到今日行动清单前；派单提问改成卡片最后一块浅红底色块，「❓ 要我把第 2、3 条写成任务派给 Claude 吗？」加大加粗，下一行写怎么回（加了「不派就不用回」）。预览 `om_x100b608b3ecc94a0c0fc57b9a6e54b2` 回读通过、飞书截图看过。合并后要重装 `groupdigest.py`、提示词和 groupdigest-act 技能（技能改了要重启网关）。
- **常浩看预览后再改**（11-17）：思考和行动之间也加分割线；每条信息下面加「原文与译文 · 展开」折叠面板（X 卡片取中文和 Original，群成员发言取原话，源头取开头节选，每条最多 3 个出处、每个出处最多 700 字，卡片超过 27000 字节就逐步收紧），后面跟「原地址」链接。内容标准改成只写值得学的（常浩原话：「我只关注我能学到什么，运用什么，掌握什么，而不是简单的了解消息」，又补「哪怕我当前学不会，用不上，但是也需要学习的内容都是需要的」）：提示词、skill、工具的分享和用法，方法论、工作流、操作路径，技术教程和复盘；纯资讯（新模型上线、谁被入侵、马斯克说什么）不写，行动和思考也只从值得学的内容来，一天全是资讯就一条不写（信息条数下限从 1 改成 0）。
- **按新标准重写 11-16 试跑**（另存 `evidence/192-groupdigest/learn-trial-1116/`，没动已发的那份）：一次通过核对，Cursor 被入侵、Qwen 和腾讯新模型、Codex 报错这几条资讯都没写；剩 5 条：宝玉的多模型分派法、Ethan 的视频转 3D 提示词、Next Token Skill 的写法、强化学习交互讲解、Claude Code 主题 mod；行动 3 条（1 条标 Claude）、思考 3 条。预览 `om_x100b608beb114050c33f1bfb7ce189f` 回读通过；上一版预览在飞书截图看过，面板能展开收起、原地址跟在后面；最后一版加的右侧下拉箭头没在界面里核对（常浩当时在用飞书）。
- **合并与重装**（常浩 11-17 回「合并并重装」）：PR #194 CI 通过（本机 Python 3.9 全量 1034 项、orca 也通过），squash 合并为 `e021daa`；重装 `groupdigest.py`、提示词、groupdigest-act 技能，SHA-256 与 main 一致，旧版备份在 `evidence/192-groupdigest/install-20261117-150427/`；两个入口脚本没变；网关重启后 15:04:35 飞书重连（新进程 90237），技能已启用，两个定时任务仍是 active，下次 11-18 10:00 / 10:45。
- **唯一的下一步**：常浩在 11-16 那张卡上回一次「派」（或「派 2」）验证派单；11-18 起看 10:45 卡片是否按时到；连续 3 天按时到达再验收。

---

## 2026-11-14 · #133 托管账本、额度、汇报卡与推翻（PR #186 已合并 `90fd20a`，未安装）

- **常浩 11-14 定**（回「按推荐开工」）：试发汇报卡要他单独说「试发」；策略第 2 节的额度上限签署时就校验；合并类推翻在本单开 revert PR、合并仍由他点卡片。汇报卡样式选乙版（表格，三版预览 https://claude.ai/artifact/SHmRwL7XMmh8SdMKYcSjzV）。
- **账本**：`custody_ledger.py` + twin-state v4（`custody_decisions`、`custody_quota`，每次变化同事务记 `custody.decision.*` 事件）；原文存 `DigitalTwin/custody/<托管号>/`，90 天后清。回复、合并要托管准入和策略允许，额度在写锁里原子预占；结果不明冻结、对账后才计为已用或释放；撤不回的动作只能记成挂起。
- **排空**：`custody.drain_proof` 读账本，先作废已出未送达的决定，再核对没有在途、结果不明、UNRECONCILED，`off --drained` 不再一律拒绝。
- **行为变化**：策略第 2 节的额度上限读不出、越界就判策略无效（签署时拒绝）；merge-buttons 插件多认托管的两个按钮（合并 / 退回不变）；twin-state 库升到 v4（只加表）。
- **汇报与推翻**：`custody_report.py` 收尾后发卡、回读、同一收尾只发一次；推翻只认常浩私聊、只认发过的汇报卡，记「已推翻·已记录」、写规则补丁候选、列下游影响清单（`custody_impact.py` 四类规则，取不到写「没列全」）；合并类由合并执行器开 revert PR（`merge_act.run_custody_revert`，只开不合），再发「合并 revert PR」按钮卡。
- **试发**（常浩 11-14 回「试发」）：临时库造一次托管（挂起 1、回复 2、合并 1、作废 1），乙版卡发常浩私聊，回读是卡片、含托管号；首次被飞书拒（表格自定义列宽要 80–600px，原来 70px），改 80px 后发出，已加回归测试。卡片 JSON 和回读在 `evidence/133-ledger/trial/`；卡上按钮在插件安装前点了没反应。
- **白话改写**（常浩 11-14：「卡片格式没问题，但看不懂到底做了什么，就没法判断」，回「按改写后改」）：标题说替你定了几件、几件等你处理；开头一句说托管替你做了什么；「等你处理」「替你定了的」分两张表，列名改成哪件事 / agent 问你什么 / 我怎么处理的（我替你定的）/ 为什么 / 后来怎样 / 你要做什么（你不同意怎么办）；「后来怎样」现读夜跑记录，读不到写「还不知道」；按钮写「推翻：重试 3 次」；去掉挂起、作废、HEAD、L1、D 档、决定号等术语。账本加「这件事在做什么」「常浩要做什么」两项，五项写给常浩的字按动作必填、限长、不许术语（不合格不记账，#134 照这个写）；推翻、撤销 PR 的私聊回复也改成白话。规格 §3.6 的「撤回能力」照样记账，卡上显示为「你不同意怎么办」，规格文档没改。改写后再试发一张（`om_x100b60b41de670a4c06419721a9b128`，回读通过）。对照页 https://claude.ai/artifact/SHmRwL7XMmh8SdMKYcSjzV
- **没做**：没安装、没装定时任务；谁在收尾后调 `custody_report.py send` 由 #134 接；规则补丁候选接进每周提案另议。
- **L3 第 1 轮（11-14，接力 `h1a1d8c65cbc04`）**：返修，2 P0 + 8 P1 + 4 P2，已全部返修：
  - P0-01 / P1-08：「合并撤销 PR」改走 `merge_act.merge_revert_as_owner`，和飞书卡片合并同一套检查（含 CI 必须通过、敏感路径拦下；撤销 PR 没有 CI 也停），只认最新一张、回读核实过的撤销卡。
  - P0-02：账本库不在就报错、不自动新建；账本有身份（`L-…`），开启托管时登记并记进会话；排空证明、汇报都核对身份和登记，原文有、账本没有的算没收完；托管开启时库不在就不开。
  - P1-01：项目只认名册里的沙盒，子单号只认规范写法（`DTS-9`），合并要完整仓库名、整数 PR、40 位提交。P1-02 / P2-02：合并做成必须带合并提交号；对账证据改成结构化字典。
  - P1-03：推翻分六个阶段、可恢复（`<决定号>.overturn.json`），再点或 `custody_report.py resume <决定号>` 只补做没完成的；合并没核清时「等核清再开」，撤销 PR 开没开成不确定时只按分支只读查、不再推送。
  - P1-04 / P2-03：汇报卡每张一个随机令牌，按钮带令牌（插件 `--card` 转交），回读核实才算发过，核实不了先按原消息号回读再重发；只认卡上真有按钮的决定。
  - P1-05 / P1-06：影响清单 PR 翻完所有页、以 PR 全部提交为种子；夜跑记录坏行、标了不完整、PR 被强推过都写「没列全」。
  - P1-07：每次进入排空 / 冻结都尽力清账（锁外，账本出错不挡关闭），汇报卡发送前再清一次。**#134 接夜跑主控时，「每次进入排空都清账」是阻断性集成测试。**
  - P2-01：记账、开始送达拿到写锁后才取时间。P2-04：汇报卡只发常浩私聊。
  - 测试：托管相关从 111 项增到 140 项（3.9 也过），补了库消失、旧备份恢复、额度别名、回读失败重发、推翻每阶段中断、结果不明合并对账后推翻、撤销 PR 无 CI / 碰敏感路径被拦、坏夜跑记录、PR 翻页、强推。
- **L3 第 2 轮（11-14，接力 `h1a1d90c43af00`，提交 `89d021a`）**：需修改、决策升级，剩 3 个 P1、2 个 P2；常浩选 A「定向修复并复核」：
  - P1-01：撤销 PR 已开、结果没落盘就中断时认领不回 → 开 PR 前记操作凭证（决定号、仓库、分支、合并提交号），续做时只读查分支、核验提交说明写着回退这次合并提交、只有一个提交、PR 正文有决定号和合并提交号，唯一才认领，不再推送。
  - P1-02：账本身份没覆盖全部入口 → 汇报（含「已经发过」提前返回）、推翻、合并撤销 PR 都先过 `check_ledger`（身份、开启登记、没有有原文没账本的）；挂起只能记进现在这次托管。
  - P1-03：撤销 PR 只有跳过 / 中立的 CI 也能过 → 另要求 `merge_rules.json` 的 `revert_required_checks` 里登记的测试检查真的 SUCCESS，没登记就停。dt-sandbox 现在没有 CI 工作流，所以撤销 PR 的合并按钮现在都会停下，要沙盒配好 CI 并登记检查名才能用（`merge_rules.json` 这次没改）。
  - P2-01：命令被拒但到点推进进了排空，也锁外清账。P2-02：汇报卡 `--force` 重发后只认最新一张；回读要核到托管号、页脚令牌和按钮数。
- **定向复核（11-14，提交 `a61dcf9`，回信 17:18；驿窗没自动转回，18:18 从交接记录读到）**：需修改、决策升级。P1-02、P1-03、P2-01、P2-02 通过；P1-01 剩一处：认领靠的提交说明、PR 正文都是能手写的文字，中断在结果落盘前时没有本地提交号可比，`gh pr list` 也没核来源仓库、没设上限。沙盒的另一个写权限账号就是夜跑 agent 用的 `shanhao1997`，伪造方正是托管要防的对象。原文 `evidence/133-ledger/review2/reply-round3.md`。
- **常浩选 A「补上再合并」（11-14），补改未复审**：`run_custody_revert` 加 `on_head`，推送之前把本地撤销提交号落盘，落盘失败就不推；所以没记下提交号＝一定没推过，续做时重新开。记下了就只读查，认领要求 PR 的 HEAD 等于记下的提交号、来源分支就在目标仓库本身（`same_repo`：不是 fork），提交说明和正文降为附加核对；`gh pr list` 设上限 100，到上限算查不全、保持不确定。执行器开完 PR 的回读也核来源仓库。新增反向测试：同名分支说明正文都对但提交号不同、fork 同名分支、仓库名不对、查不全、中断在落盘前重开、推送前已落盘、落盘失败不推、回读来自 fork；拿掉提交号和来源仓库两项检查，前三个反例都失败。
- **已合并**（11-14 常浩说「合并」）：CI run 43586235833 通过后 `gh pr merge 186 --squash --match-head-commit 8fdc802…`，合并提交 `90fd20a`，#133 自动关闭，主目录已拉齐。
- **安装副本落后于 main（按约定没装）**：`~/.hermes/scripts/` 的 `merge_act.py`、`custody.py`、`twin_state.py` 和 `~/.hermes/plugins/merge-buttons/` 与 main 不同；`custody_ledger.py`、`custody_report.py`、`custody_impact.py` 没装。每天 10:00 工程自检会报这几处不一致，是预期的。几处装起来有连带：`twin_state.py` 装上后打开状态库就升到 v4（只加表）；`custody.py` 装上后开启托管要账本库和 `custody_ledger.py` 在，否则拒绝开启。
- **常浩 11-14 定「等一起装」**：这 4 个已装文件和三个新脚本等 #134 / #136 一起装、一起验；在那之前工程自检报的这几处不一致是预期的，不用处理。
- **唯一的下一步**：接 #136 托管合并（硬性前置：不在宿主执行 PR 里的脚本）。

---

## 2026-11-14 · #174 运行前身份闸已合并并安装（PR #175 `351d444`）

- **合并**（常浩经 Agent HUD 回「合并 #175」）：L3 首轮需修改 → 第 2 轮需修改 → 第 3 轮（常浩授权）需修改 → 专项复审（常浩授权）通过，0 P0/P1/P2；由 #175 会话（`local_a838a099`）返修。变基到 main `7b425af` 后头部 `4417466`，CI 通过，`--match-head-commit` squash 合并；之后 main 前进的 `a07a4ea` 只改 `ops/self_check.py`、CURRENT，与本 PR 无交叉。#174 自动关闭。
- **内容**：`multica_night.py` 开跑前和每次放 stage 前都跑身份闸，fail-closed。允许的账号、仓库、执行器容器、保护基线来自名册 `projects.json` 的 dt-sandbox `executor_gate` 和 `hermes/baselines/dt-sandbox-github.json`，只读 origin/main 上已合并的版本，不读工作区；宿主用常浩的 gh 只读比对规则集和 main 分支保护；容器里结构化采集 gh 账号、权限、凭据来源、工作区 git 配置（含嵌套仓库）；冻结命令只按名册生成，不再用计划里的 `freeze_cmds` / `freeze_check`。测试 hermes 900、orca 48 通过；真机两次只读复核通过。
- **安装**：`~/.hermes/scripts/multica_night.py` 已同步（`cmp` 一致；旧副本在 `evidence/135-defense/identity-gate-install/`）。环境变量 `DT_ROSTER` 改为 `DT_ROSTER_REPO`：本机定时任务、网关 plist、`~/.hermes/.env` 里都没设过 `DT_ROSTER`，无需改（`dispatch.py` 等其他脚本仍用 `DT_ROSTER`，不受影响）。用网关 plist 的环境变量实跑一次：名册条目找到，基线规则集 `[26743254, 26932758]`、main 分支保护，宿主 gh 只读比对「与基线一致」（`host-gh-in-hermes-env.log`）。脚本由夜跑定时任务按需调用，不用重启网关。
- **范围外（PR 里写了）**：Multica-local「E2-2 登录GitHub沙盒令牌」/ `runtime/login-gh.sh` 仍可能把常浩账号引进容器，只能在下次放 stage 前被身份闸发现；两次检查之间靠容器里拿不到高权限凭据。
- **唯一的下一步**：等 PR #173 第 2 轮 L3 结论 → 常浩说「合并」后合并并按 `ops/multica-egress/README.md` 核对安装（本机已装的是返修后的版本）→ 请常浩决定 #135 是否重新验收。

## 2026-11-14 · 记忆日报 11-12 验收通过；11-13 唤醒后网络没通没写成，已补写；非 JSON 网络错误重试修复已合并并安装（#178 `c7e00a2`）

- **11-12（验收第 5 项，通过）**：11-13 电脑睡眠错过 07:30，09:56 跑；`2026-11-12.delivery.json` `read_back: true`、`pushed_at: 2026-11-13 09:56`，文档顶部依次是 11-13、11-12、11-11、10-27。
- **11-13 首跑失败**：11-14 07:45 跑时网络没通，`lark-cli` 刷新常浩用户令牌失败（`dial tcp`），stdout 空、stderr 不是 JSON，`lark()` 直接报「输出不是 JSON」没重试（#170 只覆盖 JSON 里的网络错误）；失败通知也因 `open.feishu.cn` 连不上没发出去（Hermes cron 记 `live adapter send failed`）。
- **已补写**：09:40 `hermes -p agumon cron run 45590a963269`（安装中的代码）：会话 `20261114_094022_5fb12e`，gpt-6.1-sol 一次过核对，9 张表，09:49 写入、回读通过、已推送（`2026-11-13.delivery.json`）。
- **修复（分支 `claude/memdaily-wake-retry`，待合并）**：`memdaily_data.lark()` 输出不是 JSON 时也按网络错误判断；`NETWORK` 加 `dial tcp`、`token refresh request failed`；间隔改为 10、30、60、120、120 秒（共约 5 分钟，同 #176 pdaily 的唤醒等待），写文档仍 `tries=1`。新测试用 11-14 的真实报错原文，在旧代码上报错、新代码通过。
- **已合并并安装**（常浩经 Agent HUD 回「合并」）：CI 通过后带 `--match-head-commit 190f55c` squash 合并为 `c7e00a2`；安装前备份 `evidence/memdaily-wake-retry-20261114/memdaily_data.py.before`，装到 `~/.hermes/profiles/agumon/scripts/memdaily_data.py`，`cmp` 一致（`memdaily_publish.py`、`memdaily_job.py` 也一致），安装副本导入自检正常。无模型定时任务每次现读脚本，不用重启网关。
- **唯一的下一步**：无；记忆日报照常每天 07:30 跑。待办：接管《记忆周报汇总》（`KD1WdH6G6o…`），删测试文档 `BdqPdZsc…` 前先问常浩。

## 2026-11-14 · 个人项目日报连续三期降级查因；#176 核对与取数修复已合并并安装（PR #177 `801bd60`）

- **11-20 续**：11-20 代验时工程自检报的 7 项安装副本不一致已按常浩方案收口，见最上面「#251」一节。
- **来由**：常浩问 11-13 的日报为什么降级。查实 11-12、11-13、11-14 三期（统计日 11-11 / 11-12 / 11-13）都发的是「脚本事实版」。
- **主因：Hermes 调不动 Claude，回退 deepseek-flash**：Hermes 最后一次成功调 Claude 在 11-11 10:32；11-12 写稿报 `ClaudeAPIError`（`HERMES_MODEL_ADMISSION_CONSUMED`，重试 3 次），11-13、11-14 报 `ClaudeCodeLoggedOut`（OAuth 过期刷新失败）。`ai_access.py status` 显示 Claude Code「未登录」、近 7 天回退 23 次（飞书对话同样在回退）。deepseek 写的稿核对不过，整份降级。**未修**：要常浩本人 `claude auth login`。
- **副因（11-12、11-13）**：09:15 电脑在睡眠，09:38 / 09:36 醒来才跑（`pmset -g log`）；刚醒代理没通，16 个项目里 15 个 GitHub 取不到（connection reset / EOF / TLS 握手超时）；核对又把 null 当「没合并」，模型写对的推进被判错。
- **#176 修复（PR #177，CI 通过，`--match-head-commit 0e464f2` squash 合并）**：`pdaily_publish` 合并或关闭记录取不到时不判「没有实质推进」；`pdaily_data.GitHub` 网络类失败每 15 秒重试，整轮最多 `DT_GH_WAIT_SECONDS`（默认 300）秒，只在日报取数入口启用。全量 hermes 847、orca 48 通过。回放真实快照：统计日 11-11 核对 3 处 → 0 处，11-12 4 处 → 1 处，11-13 两边都是 2 处（剩下的是 deepseek 在「今日推进」里没写项目名）。证据 `evidence/176-pdaily-null/`（全量输出、回放脚本、安装前备份 `install-before/`）。
- **已安装**：`pdaily_data.py`、`pdaily_facts.py`、`pdaily_publish.py` 装到 `~/.hermes/scripts/`，逐个 `cmp` 一致；`/usr/bin/python3` 和 python3.14 导入自检正常；安装副本实跑 `--date 2026-11-11 --project digital-twin` 取到 9 个合并 PR（#152–#168），和 GitHub 一致。无模型定时任务每次现读脚本，没重启网关。
- **同日另做（驿窗）**：侧栏猛鬼兽定时任务表把「个人项目日报·写稿 / ·发送」合成一行（postpane #326，PR #329，已装机 `c4e1930394b2`）。
- **Claude 已重新登录（09:46）**：常浩前两次在终端登录，浏览器都显示授权成功，命令行却没登上；查实是终端面板的 shell 没有代理变量，命令行直连换凭据被拒，返回 `Login failed: Request failed with status code 403`。带上 `HTTPS_PROXY=http://127.0.0.1:8086` 重登后显示 `Login successful.`，`claude auth status` 为 `loggedIn: true`（claude.ai，max）。用干净环境、照网关的代理设置跑 `hermes chat -Q` 单次调用，会话 `20261114_094741_dec7f3` 实际用的是 `claude-opus-5-5[1m]`，没有回退。注意：在带桌面应用环境变量（`ANTHROPIC_BASE_URL`）的 shell 里测 Hermes，插件会拒绝走登录态（`OAuth provider refuses conflicting native auth/backend overrides`）、回退 deepseek，这是测试环境的问题，不是故障。今天 10:00 发的仍是 09:19 写好的事实版。已记为全局坑 G-27（agent-kit PR #68 `75df67e`，运行副本已重装，`auth login` 等触发词会自动提醒）。
- **#180 工程自检加 Claude 登录检查（PR #181 `7b425af`，CI 通过，常浩「合并并安装」）**：Hermes 主模型走本机 Claude 而 Claude 不可用时，每天 10:00 日报「🤖 AI 接入提醒」报后果和带代理的登录命令；复用自检已在跑的 `ai_access.py status --json`。全量 hermes 850、orca 48 通过。**已安装** `~/.hermes/scripts/self_check.py`（`cmp` 一致，旧副本在 `evidence/180-selfcheck-claude/install-before/`）；网关同款干净环境实跑，Claude 登录正常时这一项无输出。G-27 升级改为〔体检〕部分升级（agent-kit PR #69 `c93932f`，运行副本已同步）。
- **#182 自检误报已修（PR #183 `8adaba3`，CI 通过，常浩「合并并安装」）**：自检把亚古兽的 4 个记忆日报文件按猛鬼兽目录比，天天在日报里误报「安装副本缺失」（亚古兽副本其实和 main 一致）。改为 `memdaily_*` 比 `~/.hermes/profiles/agumon/` 下的副本；全量 hermes 851、orca 48 通过。**已安装** `~/.hermes/scripts/self_check.py`（`cmp` 一致，旧副本在 `evidence/182-selfcheck-agumon/install-before/`）；网关同款干净环境跑整份自检无输出（全部正常）。
- **唯一的下一步**：11-15 09:15 写稿后看 `DigitalTwin/daily/2026-11-14.build.json` 的 `model` 是不是 `claude-opus-5-5[1m]`、`degraded` 为 null。可选：常浩自己设 `sudo pmset repeat wake MTWRFSU 09:10:00`，让 09:15 不被睡眠推迟。

## 2026-11-14 · #131 托管状态底座已合并并安装（PR #167 `85611f7`）

- **内容**：`hermes/scripts/custody.py` 是 `custody_state = off | on | draining | frozen` 的唯一真源，只有它写 `DigitalTwin/custody.json`（逐字段校验、原子替换 + 排他锁、写过后文件丢了不当成第一次用）。开启只认飞书私聊（twin_identity 回读核验 + 口令原文 + 防重放，时刻有歧义就拒绝、最多 24 小时）和已签策略的默认时段；关闭 / 撤销任何来源都可以；到点自动排空、到排空截止没收完自动冻结，冻结只能私聊「解除冻结」解除。策略签署：不许未填项、字段位置固定且唯一、版本单调、全文 SHA-256；`revoke` 持久撤销。`status --json` 带统一准入判定 `admission`，下游不得只看 `state`。工程自检只读显示托管状态。`twin_identity.lark_fetch` 加可选 `with_text`（默认不变）。接口要点给 #133 / #134 / #136 的在 custody.py 头注释。
- **审查**：L3 三轮（ChatGPT High，接力 `h1a1cef2477e01`）。第 1 轮 8 P1 + 3 P2 全部返修；第 2 轮新提 1 P1（会话 ID 只到秒）+ 3 P2；第 3 轮（常浩 11-14「补改并续审一次」）全部关闭、通过。全量 842 + 48 通过，CI 通过；常浩「合并」，`--match-head-commit 8ac42bb` squash 合并。证据 `evidence/131-custody/`（全量输出、变异检查、lark-cli 时区实测、第 3 轮送审包）。
- **已安装**（常浩「安装」）：`custody.py`、`twin_identity.py`、`self_check.py` 从 main 装到 `~/.hermes/scripts/`，逐个 `cmp` 一致；旧副本备份在 `evidence/131-custody/install-before/`（回滚：拷回这两个、删 `custody.py`）。`/usr/bin/python3`（3.9）和 python3.14 下 `custody.py status` 都是「关着、还没签策略」、不准入，没生成 `custody.json`；twin_identity / twin_state 导入正常。脚本按需调用，没重启网关。工程自检里这三个已不报；仍报 4 项 `memdaily_*` 缺失——它们 11-12 装在亚古兽目录 `~/.hermes/profiles/agumon/`，自检只看 `~/.hermes/`，属自检没认亚古兽安装位置（11-12 起每天误报，未处理）。输出在 `evidence/131-custody/install-self-check.txt`。
- **上线前还差**：`off --drained` 现在一律拒绝（`drain_proof` 等 #133 接账本），每次托管会以冻结收尾；本机还没配 `owner_chat_id`（`twin_identity.py setup-chat`），飞书开启 / 签署 / 解除冻结都会被拒；到点自动开关要定时任务（D 档，另问）。
- **驿窗问题（范围外，没修）**：续审授权后接力立刻被判「Claude 2 小时没交回复审」停下（计时从两天前的上一轮回信算起），之后 Chrome 插件也断过；常浩点「继续自动」并修好插件后才送出。属 postpane。
- **唯一的下一步**：托管主线下一项是 #133 账本（在 `custody.drain_proof` 里接账本）。另：工程自检要认亚古兽的安装位置，否则每天误报 4 项 `memdaily_*`。


## 2026-11-12 · #132 合并隔离已合并并安装（PR #146 `0a70219`）

- 甲方案落地（常浩 11-09 定、11-10 实施）：agent 改用机器账号 `shanhao1997`（dt-sandbox 写权限，经典令牌 `repo`+`read:org`，2027-02-08 到期，常浩在容器里亲手登录）；dt-sandbox 新规则集 `main-merge-reserved-to-admin`（`update`，仓库管理员 `exempt`）；合并执行器专用令牌在常浩账号下只对 dt-sandbox，存钥匙串（service `dt-merge-executor`），约 2027-02-08 到期。12 项反向测试全部符合预期，见 `docs/DT-ORC/migration/2026-11-10-合并隔离实测.md`。
- ADR-014 修订 2 / ARCHITECTURE V5.1.1（常浩 11-10 选「按推荐改措辞」）：合并凭据只由合并执行器使用，**受限会话**拿不到；同一 macOS 用户下的全权进程也能读到，隔离边界是受限容器和 GitHub 服务端。
- `merge_act.py`：新增托管合并唯一入口 `run_custody_merge`（钥匙串强制取令牌、最小环境、REST 合并接口以本次合并提交为证据、`merged / failed / unsure`），卡片合并改走 `run_owner_merge`、行为不变。L3 三轮（第 3 轮常浩授权，通过）+ Codex 独立审查；全量 775 + 48 通过（变基后 HEAD `8656050`）。**已安装**到 `~/.hermes/scripts/merge_act.py`（旧副本备份在 `evidence/132-merge-isolation/`），导入与 `cards` 回读正常；脚本由插件按需调用，不用重启网关。
- 交给后续：#136 不得在宿主执行 PR 里的测试 / 安装脚本，只调 `run_custody_merge`；#135 补测见 #135 评论。Multica-local 的「E2-2 登录GitHub沙盒令牌」脚本仍会引导用常浩账号登录容器，换令牌时改用机器账号（#174 身份闸挡住）。

## 2026-11-12 · 记忆日报重试修复（#170 `a6c1912`）和亚古兽人设 V4（niuma-home#57 `c201ad0`）已合并并安装

- **合并**（常浩「合并」）：两个 PR 的 CI 都通过，带 `--match-head-commit` squash 合并。
- **安装**：先备份到 `evidence/agumon-persona-20261112/install-before/`（SOUL.md、HERMES.md、memdaily_data.py）。装到 `~/.hermes/profiles/agumon/`：`SOUL.md`、`workspace/HERMES.md`、`locales/zh.yaml`（新建目录）、`scripts/memdaily_data.py`；与两边 main 逐个 `cmp` 一致，`memdaily_publish.py`、`memdaily_job.py`、`prompts/memdaily_report.md` 也核过一致。人设三份 SHA-256 与送审说明一致。
- **重启**：10:56 `launchctl kickstart -k gui/$(id -u)/ai.hermes.gateway-agumon`，10:57:11 `✓ feishu connected`。定时任务 `45590a963269` 仍 active，下次 11-13 07:30。
- **回滚**：`install-before/` 里的三个文件拷回，删 `~/.hermes/profiles/agumon/locales/`，重启亚古兽网关。
- **飞书实测**：11:09 常浩发「你好啊」走的是 11-02 开的老会话 `20261102_180956_56cddf01`，Hermes 对继续中的会话沿用存下的系统提示（`agent/conversation_loop.py` `_restore_or_build_system_prompt`），查到仍是旧人设，不算数。常浩发 `/new` 后新会话 `20261112_112402_fd35aaa1` 的系统提示含「思维内核」「工作动态与记忆日报」、无旧「性格与表达」；问「调研的方法论是什么」，首句加粗给结论（「调研的本质，是用证据减少关键不确定性……」），正文约 350 字（去掉空白和标记），模型 gpt-6.1-sol。新沉淀提示「📌 方法沉淀｜…」要等下次沉淀才看得到。
- **唯一的下一步**：11-13 07:30 后看《记忆日报汇总》顶部有 11-12、私聊收到推送、`DigitalTwin/memdaily/2026-11-12.delivery.json` 的 `read_back` 为 true 且 `pushed_at` 有值。之后接周报《记忆周报汇总》（`KD1WdH6G6o…`），删测试文档 `BdqPdZsc…` 前先问常浩。

## 2026-11-12 · 记忆日报首跑失败已补写；网络重试修复待合并；亚古兽人设 V4 待确认（niuma-home#57）

- **首跑**：定时任务 `45590a963269` 11-12 09:12 才跑（07:30 电脑在睡眠），读《记忆日报汇总》网络超时，没写成，原因发到亚古兽私聊。手动重跑又遇飞书「could not export … Retry the export」。两种都是一过性错误，原重试只认飞书标的 retryable。
- **修复（分支 `claude/memdaily-retry`，待合并）**：`memdaily_data.lark()` 对网络超时、连接断开、「Retry the export」也重试，间隔 10、30、60 秒；写文档的调用仍 `tries=1` 不重试。新增 2 项测试（共 20 项）；全量 hermes 767、orca OK。
- **11-11 已补写**：用修复后的分支代码手动跑 `run --date 2026-11-11`：gpt-6.1-sol 一次过核对，8 张表，10:15 写入正式文档、回读通过、已推送（`DigitalTwin/memdaily/2026-11-11.delivery.json`）；牛马之家试读最新一篇为 11-11，共 136 篇。
- **人设 V4（niuma-home PR #57，待常浩确认）**：SOUL 加「思维内核」（五个核心模型，第一性原理为主轴、可显性外化），对话默认精简版（≤300 字）、常浩本人请求不要凭证、自我认知以实查为准、「沉淀」；HERMES 加 §6 工作动态与记忆日报；新增亚古兽专属 locales（截图里「工间记一笔」是猛鬼兽的覆盖文件）。临时 profile 试答：新版 957 / 778 字 vs 现版 3060 / 2589 字。底线逐字未改。临时 profile `agupreview` 已删。
- **唯一的下一步**：常浩确认人设 V4 和重试修复 → 合并 niuma-home#57、本仓库重试修复 PR，安装（亚古兽 SOUL、workspace/HERMES.md、locales/zh.yaml；scripts/memdaily_data.py），重启亚古兽网关，飞书私聊实测一句；明早 07:30 后看 11-12 日报。

## 2026-11-11 · 亚古兽改用 Codex 6.1 sol xhigh；记忆日报改由亚古兽写并推送（PR #168 `5c44dae` 已合并并安装，07:30 定时任务已建）

- **模型已切换**（常浩「把 codex 的 6.1 sol 极高接进来，不用 claude 的额度」）：`hermes -p agumon config set` 改 `model.default=gpt-6.1-sol`、`model.provider=openai-codex`（`base_url` 随之清掉）、`agent.reasoning_effort=xhigh`；辅助任务 luna、回退 deepseek-flash 不变。改前配置在 `evidence/agumon-codex-20261111/config.before.yaml`。14:03 重启 `ai.hermes.gateway-agumon`，14:04 起来，日志 `Model context warmed: gpt-6.1-sol`、飞书已连。不带参数 oneshot 实测：会话 `20261111_140439_8688b0`，模型 gpt-6.1-sol、`openai-codex`、`subscription_included`、有思考 token。飞书私聊里还没实测。猛鬼兽不动，仍是 Claude。「极高」按 Codex 的 `xhigh` 理解。
- **文档**：PRD F10.1、F10.2、D20，AGENTS「AI 接入」，ai_access.json 同步（分支 `claude/agumon-codex`，worktree `.claude/worktrees/agumon-memdaily`）。
- **常浩定（D20 修订）**：牛马之家的工作日报以后只由亚古兽写，由亚古兽推送给常浩。
- **Aily 交接（核实过）**：Aily 回复和交接文档 `Kzqvd2fMRoWncMxnsuUcInCunOd`（本地副本 `evidence/agumon-codex-20261111/aily-handoff.*`）。A 编号台账式日报不是 Aily 写的，是本仓库并跑（`report_data.py`、`daily_report.md`，文档 `WZyhdQdM0o…`）；Aily 自己的是《记忆日报汇总 v4.0》（六模块、D/I 决策洞察编号，10-28 起 14 条自动化全停）。**牛马之家日报页、周报页实际读的是 Aily 的记忆日报 / 周报**（`report_doc_url` = `TCG9dy7Uco…`、`weekly_doc_url` = `KD1WdH6G6o…`），停在 10-27；之前「牛马之家读并跑文档」的说法不对。`/api/reports/source-preview` 试读并跑文档：12 篇、最新 11-10、无未识别。Aily 可借鉴的取数规则：6 个白名单群全读、私生活与闲聊硬过滤、日历有≠实际参与。
- **人设问题（11-10 真实对话归纳，待改，源文件在 niuma-home `docs/亚古兽/角色配置-V3/`）**：不清楚自己能用什么（说没有命令工具又改口、凭空报模型）；常浩本人的请求也找常浩要权限和 token（把 SOUL 里「他人不能驱动本机能力」用到了常浩身上）；寒暄写成工作汇报、话术生硬；推进计划和使用反馈分不清。
- **常浩定（11-11）**：日报完全接着写 Aily 的《记忆日报汇总》`TCG9dy7Uco…`，按它的模块结构（六模块，7/15 永久模板）；A 编号台账体系不沿用。牛马之家设置不用改。7/15 块 XML 在 `evidence/agumon-codex-20261111/memdaily-0715.xml`；10-27 起的块表格是空壳（单元格空、值落在表外），写入方式要先在测试文档验证。
- **常浩定（「按推荐开工」）**：每天 07:30 写前一天、写完推送，周末没有工作往来就跳过；周报《记忆周报汇总》也接（批 3）；10-28～11-10 空档不补，从 11-11 开始；Aily 的 09:30 / 18:00 会议确认不沿用（用实际参会记录和妙记）。
- **批 1 代码（分支 `claude/agumon-codex`）**：`memdaily_data.py`（亚古兽应用、常浩用户身份取数：私聊排除机器人——联系人查不到名字的就是机器人，白名单 6 群全读，其他群只收常浩发言或被 @ 的，实际参会的会议和妙记，日程标永久不去，任务，前一篇行动 & 阻塞）、`memdaily_publish.py`（`hermes -p agumon` 单次写六模块 JSON → 核对 → 按 7/15 模板排 XML → `block_insert_after --block-id 0` 插顶部，写前整份快照、同一天不重写、回读标题 / 表格数 / 空单元格 / 列宽 → 机器人私聊三段式推送，同一天只发一次）、`memdaily_job.py`、提示词 `memdaily_report.md`、测试 `test_memdaily.py` 18 项（7 处反向改坏全抓到）。本机配置 `DigitalTwin/memdaily/config.json`（白名单群 chat_id、永久不去的会议、重点人、不追踪话题）和模板样例 `template-0715.md` 不进仓库。
- **实测**：测试文档 `BdqPdZscLoWwkQx7AzMcAXeDnsb` 上 `block_insert_after --block-id 0` 能插到顶部、`<col width>` 生效（接口读回 `[55, 411, 107, 190, 168, 207]`，与 7/15 原文 8 张表逐张一致）。11-10 真实数据样稿：取数 27 秒（私聊 16、机器人私聊 3 个排除、群 3、消息 419 条），写稿 gpt-6.1-sol xhigh 一次过核对、440 秒，会话 `20261111_210718_c6c48a`；写入测试文档回读通过；浏览器里六模块都正常，抽查 8 条原话都能在材料里找到。决策表 ID 列 55 宽会把编号折成 4 行，7/15 原文也是 55，没改。
- **已合并并安装**（常浩「认可，合并并安装」）：CI 通过后带 `--match-head-commit 24abf24` squash 合并为 `5c44dae`。装到亚古兽 profile：`~/.hermes/profiles/agumon/scripts/` 下 `memdaily_data.py`、`memdaily_publish.py`、`memdaily_job.py`、`report_data.py`（取数复用它的消息清洗），`prompts/` 下 `memdaily_report.md`、`project_vocab.md`，逐个 `cmp` 一致（原来没有同名文件）。用 Hermes 自带 Python 3.14.7、`HERMES_HOME=agumon` 实跑安装副本取数，结果与仓库版一致。
- **定时任务**：亚古兽 profile `45590a963269`「记忆日报」，`30 7 * * *`，无模型，脚本 `memdaily_job.py`（写前一天），没写成的原因发到 `feishu:oc_aa36…`（常浩与亚古兽私聊）；下次 11-12 07:30。任务表前后在 `evidence/agumon-codex-20261111/agumon-jobs.{before,after}.txt`。正式文档还没写过任何一篇；测试文档 `BdqPdZscLoWwkQx7AzMcAXeDnsb` 留着，待常浩同意再删。
- **还没做**：批 3 周报《记忆周报汇总》；批 4 亚古兽人设改造（niuma-home）；飞书私聊里实测亚古兽新模型。
- **唯一的下一步**：11-12 07:40 后看三处：《常浩-记忆日报汇总》顶部有「📅 2026-11-11（周三）」、亚古兽私聊收到三段式推送、`DigitalTwin/memdaily/2026-11-11.delivery.json` 的 `read_back` 为 true、`pushed_at` 有值。

## 2026-11-11 下午 · AI HOT 热点榜上线（#163 PR #164 `9165232`；chat_id 修复 #165 PR #166 `ad12512`；均已合并并安装）

- **样式定稿**（常浩 11-11「选三」，之后又定「去掉表头，同 GitHub 周榜」）：名次栏——前三灰底、奖牌 + 红色大号名次，4–10 灰色名次；每条标题（只链 aihot.news）+ 话题标签 + 浅灰概括（整句，不出省略号）；不放来源 / 参与者 / 时间 / 原文；没有表头，横幅即标题，预览文字在 `config.summary`。样稿和定稿卡片在 `evidence/aihot-card-design-20261111/samples/`（chosen-card-5c.json），之后改样式先单独问。
- **数据**：`hot-topics` + 每条 `stories/{publicId}` 的 digest 第一段；接口写明不返回热度和涨跌，不显示。纯脚本，不调用模型（AGENTS、ai_access.json 的 no_ai 已登记）。
- **安装**：`aihot_hot.py`、`assets/aihot-banner.png`、`feishu_home.py`，以及改用 `feishu_home` 的 `tidings_weekly.py`、`proposal_digest.py`、`merge_act.py`，都从 `origin/main` 装到 `~/.hermes/scripts/`，逐个 `cmp` 一致。定时任务 `198ea1ea68df`：无模型，`45 9 * * 1-5`（周一到周五，不跟调休），发常浩私聊，下次 11-12 09:45。记录在 `DigitalTwin/aihot/`（runs.jsonl、<日期>.card.json、state.json）。
- **试跑发现并修好的坑**（#165）：无模型定时任务给脚本的环境里没有 `FEISHU_HOME_CHANNEL`，第一次试跑报「没有私聊 chat_id」；**Tidings 周报 11-09 那期也因此没发出**。改为共用 `feishu_home.home_chat()`：环境变量没有就从 `~/.hermes/.env` 只读这一项。修好后经 `hermes cron run` 试跑成功（`om_x100b6074f131b8b0c0f0c43dcc1c4d0`，回读通过）；两条失败记录 `198ea1_91541f35aed5`、`18a21b_939e2b1819f2` 已 ack 关闭。全量测试 747 项通过。
- **Tidings 11-09 不补发**（常浩 11-11 定）：下周一 14:00 照常发新一期。
- **唯一的下一步**：11-12 09:45 看 AI HOT 是否自动到达（`DigitalTwin/aihot/runs.jsonl` 有当天 ok 行）；10:00 看日报卡片是否带工程自检那一行。

## 2026-11-11 下午 · 猛鬼兽定时任务按新清单整理（#159 PR #160 `967bbb4` 已合并并安装；AI HOT 三版待选；github-center#88）

- **常浩定的猛鬼兽清单**（「按推荐开工」）：日报每天 10:00（09:15 写稿，给工作日 09:45 的 AI HOT 让位）；周报卡片每周一 10:30（结构待梳理）；工程自检并进日报（正常一句、有问题一块）；Tidings 排行榜每周一 14:00（卡片格式不变）；Multica 夜跑 + 影子合并官合成「夜间托管」（待设计，两者保持停用）；skill 月度体检保留；AI HOT 日报每个工作日 09:45（新增）；其余删掉。数字分身＝猛鬼兽（个人）+ 亚古兽（企业），任务隔离；亚古兽清单常浩稍后说。
- **已删除**（任务表前后在 `evidence/mengguishou-cron-reset-20261111/`）：日报（并跑）`33df73dffd17`、周报（并跑）`6a976baa2632`、待决提案 `81464e7ea4e3`。脚本仍在仓库。剩 7 个：工程自检 `f3388d53ce97`（#160 安装后停）、Tidings `18a21b2eb87a`、影子合并官 `8994fb76d03e`（停用）、multica `6fafbfe09d8d`（停用）、skill 体检 `5aff82a706ed`、日报写稿 `945534cd43e1`、日报发送 `3a712f6cdf54`。
- **PR #160 已合并并安装**（常浩「合并并安装」，`967bbb4`）：日报发送时跑 self_check.py 并进卡片；时间文档改 09:15 / 10:00。全量 732 项通过。合并后要：装 `pdaily_publish.py`、`pdaily_build_job.py`、`pdaily_send_job.py`、`self_check.py`，规程 HERMES.md；`hermes cron edit` 两个日报任务到 `15 9 * * *` / `0 10 * * *`；停 `f3388d53ce97`。
- **AI HOT**：接口 `https://aihot.news/api/v1/dailies?limit=1` → `dailies/<日期>`，公开、每天北京时间 08:00 生成（实测 11-11）。三版卡片样稿（头条导读 / 分区目录 / 与你相关）用 11-11 真实数据：https://claude.ai/artifact/DuPJ36EeiT2wqapDPiHPCF ，数据在 `evidence/aihot-card-design-20261111/`。等常浩挑。
- **Tidings 14:00**：github-center 规定周报不早于每日更新（默认 18:00），早了自动挪到 19:30，只改设置不生效；已开 github-center#88 让周榜周一 14:00 前生成好。
- **安装**（备份 `evidence/mengguishou-cron-reset-20261111/install-before/`）：`pdaily_publish.py`、`pdaily_build_job.py`、`pdaily_send_job.py`、`self_check.py`、规程 HERMES.md，逐个 `cmp` 一致；安装位置 `run_self_check()` 跑通。定时任务：`945534cd43e1` 改 `15 9 * * *`、`3a712f6cdf54` 改 `0 10 * * *`（回读下次 11-12 09:15 / 10:00），`f3388d53ce97` 单独自检已暂停（未删）。任务表前后 `jobs.before-160.json` / `jobs.after-160.json`。没重启网关（规程每个会话现读；无模型任务每次现读脚本）。
- **技能漂移已收拾**（常浩「收回仓库保留」+「合并并按推荐」）：11-08 10:02 猛鬼兽在安装位置自改 `merge-officer`、`workshop`。确认过的两段（合并授权核对、「委托审批与授权变更」一节）收回仓库，PR #162 `3d1b76c` 已合并；`workshop` 另两处改写已定规则（未点名派单改成自选执行方、合并前提放宽到委托）按推荐丢弃，用仓库版覆盖安装副本，`cmp` 一致，原安装版备份在 `evidence/mengguishou-cron-reset-20261111/skills-before-162/`。12:10 重启网关（PID 96683 → 12480），飞书首连超时，12:14:24 自动重连成功；`self_check.py` 复跑无输出（干净）。合并委托权限留到「夜间托管」方案再定。
- **AI HOT 改方向（常浩 11-11 中午）**：内容改为 aihot.news「AI 热点榜」TOP10（接口 `GET https://aihot.news/api/v1/hot-topics`，10 条，字段 rank、title、links、sourceCount、signalCount、participantCount、sourceNames、latestAt；接口里没看到网页上的热度指数和涨跌，待核实）；卡片顶上放常浩给的「当日 AI HOT 热门榜」插画作横幅，参照 Tidings 横幅尺寸 1901×487 裁剪；出 3 版不同卡片格式，直接发飞书私聊给常浩确认。早上那个三版网页样稿作废。热点榜原始数据在 `evidence/aihot-card-design-20261111/hot-topics.json`。
- **唯一的下一步**：交接到新会话做 AI HOT 三版飞书卡片并发私聊；11-12 10:00 看日报卡片是否带工程自检那一行（应为「工程自检：正常」）。

## 2026-11-11 · 猛鬼兽只报个人项目；个人项目日报按定稿甲版每天 09:45 发（#155，PR #156 `dee0e71` 已合并并安装）

- **事故**：08:12 Claude 把 07:30 的工作日报（中台、双11、A 编号台账）套「📅 猛鬼兽日报」标题、以猛鬼兽机器人发到常浩私聊（`om_x100b600f636fb4b0dd34d3cbe1dc649`，未撤回）。常浩：猛鬼兽是个人分身，只报项目级内容，「绝对不允许再出现」。
- **排查出的设置问题**：①工作日报 33df73dffd17、周报 6a976baa2632 一直是猛鬼兽的定时任务，用猛鬼兽应用读工作私聊 / 群（与 D1、D19、D22 冲突，D22 写「和 D20 关系另定」一直没定）；②规程 §1 把工作台账文档当「正式日报」、§5 三报是工作报告；③两份都叫「日报」；④个人项目日报从未上线，11-10 定稿格式只在一张手工卡片上，代码还是每块 5 条、列宽自动；⑤Claude 写整份稿超过 240 秒被 Hermes 非流式超时掐断、落 deepseek（变量是 `HERMES_API_CALL_STALE_TIMEOUT`，不是 `HERMES_STREAM_STALE_TIMEOUT`）。
- **常浩定「只撤下不搬」**：33df73dffd17、6a976baa2632 已 `hermes cron pause`，回读 `[paused]`（任务表前后在 `evidence/mengguishou-work-off-20261111/`）；不搬到亚古兽，牛马之家日报页读的并跑文档不再更新。
- **常浩定规则**：日报每天 09:45 发私聊，格式同 11-10 定稿。PRD D20、D22、D23 已改（分支上）。
- **PR #156 已合并**（常浩「合并并安装」，`dee0e71`）：定稿格式进代码、写稿超时、`send`（云文档 + 工作内容拦截 + 发送 + 回读 + delivery.json）、`pdaily_build_job.py` / `pdaily_send_job.py`、规程 §1 §5、AGENTS、ai_access.json。全量测试 728 项通过（`evidence/daily-card-20261111/full-tests-155.txt`）。
- **已发 11-10 个人日报**：分支代码写稿（Claude，核对全过）、`send` 09:33 发出 `om_x100b600811ea5cacde34aeec47ff593`，回读通过，结构与定稿逐项一致；完整报告 `OmVrdmhX1o2TaaxKVuqcioS4nzc`。两次回退版稿子在 `evidence/daily-card-20261111/deepseek-degraded*`。客户端显示未目视核验。
- **已安装**（备份在 `evidence/mengguishou-work-off-20261111/install-before/`）：`pdaily_publish.py`、`pdaily_facts.py`、`pdaily_data.py`、`actions.py`、`pdaily_build_job.py`、`pdaily_send_job.py`、`progress_data.py`（换成 main 上 #110 版，个人日报取数依赖；progress 技能的「下一步」抽取随之更新）到 `~/.hermes/scripts/`，提示词 `pdaily_report.md`、规程 HERMES.md，逐个 `cmp` 一致。安装位置用 `/usr/bin/python3` 和 python3.14 自检：material 正常；11-10 已发过，`pdaily_send_job.py` 不重发、无输出。
- **定时任务**（无模型）：`945534cd43e1` 个人项目日报·写稿 每天 09:00，deliver local；`3a712f6cdf54` 个人项目日报·发送 每天 09:45，deliver 常浩私聊（成功无输出，没发成发一句原因）。脚本超时上限 3600 秒、写稿预算 1200 秒。任务表改前在同一证据目录。
- **网关**：09:58 `launchctl kickstart -k`，PID 76680 → 96683，09:58:06 `✓ feishu connected`。
- **已知边界**：09:45 时 09:00 的写稿还没出结果（如电脑睡眠错过 09:00），会发一张「没出成」卡片并记已发，当天不再自动补发；补发用 `pdaily_publish.py send --date <统计日> --force`。
- **写稿模型（#157，PR #158 `832049b` 已合并并安装）**：常浩问能否省额度，同一份 11-10 材料实测三版（`evidence/pdaily-model-compare-20261111/`）：Opus high 8 分 16 秒、2.8 万输出、拍板 / 成果 / 风险 8/5/13；Sonnet 5.5 high 2 分 38 秒、2.1 万、5/3/10（漏 #102、5178 终验、日报超时等）；Opus medium 3 分 01 秒、1.8 万、7/5/12、0 条截断。常浩定继续用 Opus、思考降一档：`pdaily_publish.generate()` 加 `--reasoning medium`（`DT_PDAILY_REASONING` 可改），安装副本 `cmp` 一致，`/usr/bin/python3` 读到 REASONING=medium；无模型定时任务每次现读脚本，没重启网关。全量测试 729 项通过。
- **唯一的下一步**：11-12 09:45 后看私聊是否收到 11-11 统计日的甲版卡片，并对照 11-10 定稿（`daily/2026-11-11.delivery.json` 的 sent、read_back 为 true；会话库里这次写稿的模型是 `claude-opus-5-5[1m]` 而非回退，耗时约 3 分钟）。

## 2026-11-11 · skill 月度体检排成 Hermes 定时任务（#151；PR #152 `66be6cb`）

- 常浩选方案 A（不并进工程自检）：新增 `hermes/scripts/skill_health.py`，在 my-skills 下跑 `monthly-health.mjs --days 30`，标题改粗体、表格改逐条列表，末尾加「抽查 <名字>」提示；失败退出码 1，由 Hermes 发失败通知。为 matt-flow 2.0.0 观测期配的（my-skills#86）。
- 已安装：`~/.hermes/scripts/skill_health.py` 与 main `cmp` 一致。
- 已建任务：`5aff82a706ed`「skill 月度体检（每月 1 号 10:30）」，无模型，发到和工程自检同一个飞书私聊；下次 2026-12-01 10:30。
- 08:11 手动触发一次：`last_status ok`，无投递错误，输出在 `~/.hermes/cron/output/5aff82a706ed/2026-11-11_08-11-25.md`。待常浩在飞书私聊看一眼样子。
- 遗留：`hermes/scripts/tests/README.md` 写「共 356 项」，实际 719 项，未改；`monthly-health.mjs` 会把刚上线的 skill 也列进「0 调用，可以考虑退役」，要改在 my-skills。

## 2026-11-11 凌晨 · 影子合并官暂停；猛鬼兽只读核查

- **已暂停**（常浩要求「把猛鬼兽的影子合并官的 cron 关闭」）：`hermes cron pause 8994fb76d03e`，回读为 `[paused]`；任务没删，`hermes cron resume 8994fb76d03e` 可恢复。任务表改前改后在 `evidence/mengguishou-review-20261111/`。影响：飞书不再有新的 PR 卡片，「引用卡片回合并」暂时没有新卡可用；已发卡片的按钮（merge-buttons 插件）仍可用；一致率统计停在暂停前。工程流程.md 第 5 节、AGENTS 里对合并官的描述未改。
- **影子合并官去留暂缓（常浩 11-11）**：保持暂停，文档不改。常浩想法：Hermes 自己判断、自己合并或退回，事后通知「合并了什么、理由 / 退回了什么、理由」，常浩做事后复核，不再事前点卡片确认；托管制度不推翻。现状数据：live 判定 90 条，卡片合并只用了 12 次，多数 PR 已在会话里先合；一致率 L0 6/6、L1 5/7、L2 15/15、L3 0/1，均未到 20 次，且「退回且一致」为 0（一致率只说明会合并，不说明会拦）；ARCHITECTURE 5.5 的自动合并还要求 ChatGPT High 通过和驿窗送审指纹，驿窗尚未记录，所以这条路一直走不通；digital-twin 的 `hermes/` 整体算敏感路径，相关 PR 全是 L3。优化思路和待定问题在本会话，常浩回头再定。
- **猛鬼兽优化（常浩「按建议先做」，备份和改后都在 `evidence/mengguishou-review-20261111/`）**：
  - USER.md 删掉一条重复条目，P-08～P-15 长清单压成 Vault 指针（只留每轮都用的 P-11、P-14）：1360 → 943 字（上限 1375）。
  - MEMORY.md 第一条（10-22 的配置快照，含已过期的「审批 smart」）改成「配置以 `hermes config` / config.yaml 现查为准」：685 → 551 字。
  - 规程 HERMES.md 第 1 节加「网页」：`web_extract` 抓不了正文，用浏览器工具或 agent-reach（#149，常浩「CI 过了就合并」，PR #150 已合并 `b325aa5`，安装副本与 main `cmp` 一致，worktree 已清理）。
  - **更正**：核查时说 `obsidian` 技能开着是误判，它本来就禁用（读配置时过滤掉了列表行）。中途 `hermes config set skills.disabled` 把列表覆盖成只剩 obsidian，00:24:58～00:25:30 之间 claude-code、codex 两个技能处于启用，期间无对话、无任务；已恢复，`config.yaml` 与改前逐字一致。教训：`hermes config set` 设列表是整份替换，先读全列表。
  - 重启两次（`launchctl kickstart -k`）：PID 75093 → 63206 → 63399，00:25:30 `✓ feishu connected`，三个技能回读均为 disabled。
  - **人设统一到 USER.md**（常浩「以 USER.md 为准」「删掉并改 SOUL」，11-11 07:2x；改前改后在 `evidence/mengguishou-persona-20261111/`）：
    - 删 `work-worn` 人格：`hermes config set display.personality none`、`hermes config set --force personalities {}`，配置差异只有这两处。它只作用于飞书对话（网关每轮读，定时任务不读）。
    - SOUL「性格与表达」前两句改为：打工人身份留着，「语气浓度、表情和小结格式按用户画像记忆（USER.md）里的沟通偏好」；「人设措辞每次现编…不重复固定口头禅或例句」。第三句和四条底线不动。改的是 11-02 V1.1 定稿内容，已单独确认。注入扫描 0 命中。
    - 07:27:02 重启（PID 63399 → 76746）`✓ feishu connected`。本机一次性对话实测（`env -u ANTHROPIC_BASE_URL -u CLAUDECODE hermes -z …`）由 Claude 回答，语气带打工人味，小结按「emoji＋事实＋收尾」。注意：在 Claude Code 会话里直接跑 `hermes -z` 会因继承 `ANTHROPIC_BASE_URL` 被 Claude 插件拒绝、落到 deepseek-flash，要先去掉这两个变量。
    - 代价：work-worn 里「少感叹号、不用兴奋话术、不吹嘘升级」三条没有搬走（SOUL「不刻意热情、不奉承」兜大部分）；人设只在 USER.md，后台复盘可能改动它，原文快照在证据目录。
    - 待验：常浩在飞书私聊看语气；11-11 07:30 日报是第一份按新 SOUL、Claude 跑的。
- **待观察**：11-11 07:30 日报是第一次用 Claude 跑（11-10 失败是旧模型 gpt 单次调用约 300 秒、会话租约失效被中断）。

## 2026-11-10 晚 · 亚古兽核查与修正（niuma-home#53、#54；#147、#148，均已合并）

- **已合并并安装**（常浩说「合并」）：niuma-home#54 `f859bc9`（#53 关闭，规程安装副本与 main `cmp` 一致）；#148 `4c33acb`（#147 关闭），`ops/health_check.py` 已装到 `DigitalTwin/bin/`，`cmp` 一致，旧版备份在 `evidence/agumon-hardening-20261110/health_check.py.before`；用 launchd 同款 `/usr/bin/python3` 在安装位置 `--dry-run` 正常（只报猛鬼兽昨天回退 16 次、1 条定时任务失败）。没有 kickstart 巡检任务，免得真发一条飞书；第一次正式运行是 11-11 09:45。两边 worktree 和分支已清理，主目录已拉齐（niuma-home 主目录另有他人未提交的 `docs/策略规则/README.md`，没动）。
- **唯一的下一步**：11-11 09:45 巡检后看 `evidence/health/2026-11.log` 当天一行，确认新版在 launchd 下跑通。
- **已改并生效**（常浩「按建议先做」「都按方案做」；改前改后在 `evidence/agumon-hardening-20261110/`；`config.yaml`、`profile.yaml` 是直接改的 YAML，没走 `hermes config set`，已自备份）：
  - `command_allowlist` 删掉 `recursive delete`、`delete in root path`。这两项是 14:51、15:39 常浩在飞书审批卡点「总是允许」写进去的，猛鬼兽没有；现在两只的放行清单一致。
  - `profile.yaml` 描述去掉过期的「不接触常浩个人记忆、Obsidian、ORCA」，改成记忆分开、本机能力与猛鬼兽一致。
  - 规程 HERMES.md §0：私聊和定时任务读文档改走 `lark-cli docs +fetch`（`--as bot` / `--as user` 实测都能读管培生日志）；`feishu_doc_read` 只在评论里可用（14:31 私聊调用报错）。niuma-home#54 未合并，已先装安装副本（同 #46 做法）。
  - `skills.disabled` 加 15 个和企业无关的技能：apple-reminders、imessage、findmy、apple-notes、xurl、youtube-content、songsee、gif-search、songwriting-and-ai-music、ascii-video、manim-video、p5js、himalaya、email-inbox-triage、sdlc-review（`hermes skills list` 回读均为 disabled）。
  - 重启两次（`launchctl kickstart -k`）：23:07:20 PID 27555、23:40:15 PID 49782 `✓ feishu connected`，启动无报错；第一次被「已有网关占着」拦一次，launchd 自动重试；每次重启给常浩私聊发一条停机通知。
- **#148 巡检覆盖亚古兽**（未合并、未安装）：网关、24 小时内异常退出、回退、辅助任务两只各查一遍；`~/.agents/skills` 的 lark-* 比机器人副本新时提醒同步（机器人自己改过的副本不报，亚古兽 `lark-base` 15:32 被后台复盘加了「整表重填」参考，保留）。全量测试 710 + 48 通过。回放 11-09：猛鬼兽、亚古兽都是心跳停在 04:33、07:59 拉起，两只同时断，判断是电脑睡眠。
- **常浩定**：亚古兽保留 deepseek-flash 回退，不改。更正前一版：Hermes 换到回退模型时会给对话发一条切换提示（`agent/status_output.py` `_emit_pending_fallback_notice`），不是不标注；代价是这时企业数据（管培生日志、业务表）会发给 DeepSeek。
- **常浩定：评论通道先不动**（常浩说已实测可用）。已知未改：Hermes 评论处理用 `skip_context_files=True`、没开 `load_soul_identity`，评论里不加载 SOUL / 规程 / 记忆，只有文档和评论工具、最多 15 步（`plugins/platforms/feishu/feishu_comment.py` `_run_comment_agent`）；两只机器人日志里都没找到 `Feishu-Comment` 记录，原因未查。
- **没改**：猛鬼兽 SOUL 第 3 行（已定稿，「记忆、凭据分开」仍成立）。

## 2026-11-10 · 猛鬼兽、亚古兽主模型改 Claude 会员，取消 D19（#140、#142、#144；niuma-home#45）

- **常浩定**：猛鬼兽和亚古兽都保留 Claude；亚古兽取消 D19，整个设置和猛鬼兽一致，企业 / 个人区分由常浩自己把握，「不是对于机器人的强行限制」；亚古兽的开放群直接关掉；评论区其他人拦住。PRD F10.1、F10.3、D11、D19 已加修订。
- **本机已生效**（证据 `evidence/agumon-align-20261110/`、`evidence/agumon-allowlist-20261110/`、`evidence/model-route-wait-20261110/`）：
  - 两只机器人主模型 `claude-opus-5-5[1m]`（插件 `claude-subscription-directsdk-experimental` 调本机 `claude` 命令行），思考强度 high，回退 deepseek-flash，辅助任务 openai-codex gpt-6-luna。猛鬼兽是常浩 11:43 让它自己改的，亚古兽 12:02 按猛鬼兽逐项对齐（27 项，复核零差异），并装同版插件（`a4e311e`）。
  - 飞书从 11-10 起推送带常浩企业 user_id `e9cgcf77`，Hermes 优先用它核对，两只机器人的允许名单已加上（原 open_id 保留）；亚古兽曾因此拒收常浩消息（11:40），11:51 恢复。
  - 亚古兽：开放群 `oc_08a3…` 已删、群聊关闭；飞书工具集和猛鬼兽一致，另保留牛马之家；加 Vault 读取；`workdesk-gate` 扩为他人只能用安全工具（#143，合并前已安装）。
  - 飞书用户身份（niuma-home#47）：两只机器人原来都没装飞书技能，28 个 `lark-*` 技能已从 `~/.agents/skills/` 复制到 `~/.hermes/skills/lark/` 和 `~/.hermes/profiles/agumon/skills/lark/`。lark-cli 见到 `HERMES_HOME` 会改读 `~/.lark-cli/hermes/`（只绑了猛鬼兽应用），所以亚古兽用包装脚本 `~/.hermes/profiles/agumon/bin/lark-cli`（去掉 HERMES / OPENCLAW / LARK_CHANNEL 变量，固定 `--profile cli_a965…`），这个目录放在亚古兽 plist 的 PATH 最前面；规程 HERMES.md §0 加了用法。不要用 `lark-cli config bind` 改 `~/.lark-cli/hermes/`，两只共用，会弄坏猛鬼兽。
- **已合并并安装**（常浩说「合并」）：#143 拦截插件 `b82a2c4`（安装副本与 main 一致）；niuma-home#46 亚古兽规程 `18badc5`（L1 补改未复审；已装 SOUL / HERMES 并于 12:36 重启亚古兽）；#141 等主模型通道 `b5b3f71`（探测地址按主模型提供方自动选，现为 api.anthropic.com；已装 `~/.hermes/scripts/report_data.py`）；#145 本文档同步（巡检脚本合并后同步到 DigitalTwin/bin/）；niuma-home#48 亚古兽 lark-cli 包装和规程 `3dddc63`（安装副本与 main `cmp` 一致，worktree 已清理）。#141、#143 的 ChatGPT L1 审查没有转回「需修改」；#145 送审被驿窗以「预算已用完」拒收，未审。
- **已验证**：12:28 常浩分别私聊两只机器人，都由 `claude-opus-5-5[1m]` 回复，无回退、无拒收。14:47 常浩私聊亚古兽「以我的用户身份看下销售AI侧边栏-测试反馈群」，14:52 正常回复（读群 6 次终端调用无报错、无回退）。
- **未验证**：他人在文档评论里被拦的真实端到端（要那两位成员配合）。
- **范围外**：11-10 07:30 日报 08:06 失败（`Operation interrupted: waiting for model response`）未查；亚古兽启动安全审计提示本机 SSH 允许密码登录；亚古兽 15:28 处理多维表格时两次 `cd` 到不存在的 `profiles/agumon/cache/scratch…` 目录报错，15:40 后台复盘想改 `lark-base` 技能被拒，均未查。

## 2026-11-09 · 托管规格 V5.1 已合并：通道改走 Multica issue（#130、#138；PR #139 `6bffc49`）

- **#130 探路**（记录 `docs/DT-ORC/migration/2026-11-09-托管受限会话探路.md`，`9802170`）：驿窗四条通道全被隔离容器切断；受限 agent 不在会话里停下，而是子单 blocked + 评论（24 次 run 里 5 次）；**受限令牌能自行合并 PR**（dt-sandbox 必需审批数为 0；探针 dt-sandbox#13 已由 #14 回退）。期间按常浩授权启动过 Colima `multica`，测完已停并回读，Multica issue 状态前后一致。
- **V5.1 内容**：托管只作用于正在进行、已批准的夜跑（沙盒项目），交互会话不进托管；受限会话 = 隔离容器 agent，须在任何 PR 状态下都不能自行合并（服务端强制）；夜跑主控只豁免「agent 请求决策而 blocked」一项停机原因，持锁交猛鬼兽、判断有超时；送达闸预占 run 额度，写评论前最后查 `emergency.json` / 停止 / 冻结；评论唤醒须夜跑授权写明；托管合并绑定当前夜跑来源；驿窗只读显示。ARCHITECTURE V5.1.0、ADR-014 修订 1、授权清单第 6 节、模板、PRD D24 同步。
- **审查**：ChatGPT High 共 5 轮（第 3–5 轮为常浩授权的额外轮次），最终「通过」，结论只覆盖文档规格。
- **各单已调整**：#134、#135 按 V5.1 重写；#137、agent-kit#56 关闭（不适用）；postpane#298 改为只读显示、可选；#132 提到最前；原生依赖已改。
- **未生效**：没写代码；#132 可能做不到（个人账号仓库不能限制谁能合并，候选：沙盒仓库进组织，或 agent 不直接持写权限），做不到托管就一直不启用。
- **托管线下一步**：#132 开工前先给常浩方案（要不要建 GitHub 组织或机器账号属 D 档）。项目主线的唯一下一步不变，仍是日报 2b。

## 2026-11-09 · 托管模式批 0 规格已合并（#126 / #127；PR #128 `4b0f18b`、PR #129 `8c60aed`；agent-kit PR #54 `2874edb`、#55 `94b4a2e`）

- **内容**：新增托管：常浩不在场时，猛鬼兽以常浩身份决策。PRD D24、ADR-014、ARCHITECTURE V5.0.0（4.3 `custody_state` 与执行模式正交、5.5「托管合并」、5.6 托管列、新 5.7）、常设授权清单 v1 第 6 节、`docs/托管模式-设计.md`、`docs/托管策略-模板.md`（未签，仓库里的永不生效）；D23 与夜间提案补托管例外；agent-kit《工程流程》第 8 节第 4 条加托管合并例外。
- **审查**：ChatGPT High 两轮（DESIGN / L2，接力 `h1a1bfd73e2200`）。第 1 轮 1 P0、6 P1、2 P2 全部返修；第 2 轮 P0 关闭，剩 3 项 P1 已照改（`882f620`，补改未复审，常浩 11-09 说「现在合并」）。要点：只对受限会话（受限执行身份、无合并和推 main 凭据）以本人身份送达，全权会话一律挂起；执行防线以受限身份为主、钩子为辅；合并凭据只在合并执行器；托管合并要审核凭证、当前 HEAD CI 全绿、副作用清单，只豁免 5.5 第 8 条和影子门槛；D23 没回复只做已逐项授权的单；签署绑定版本与 SHA-256；`off / on / draining / frozen` 排空状态机。
- **交付插曲**：#128、agent-kit#54 在 18:31 经飞书卡片按旧版本（第 1 轮审查前）合并；返修另开 #129、#55 绑定确切版本，CI 通过后带 `--match-head-commit` squash 合并。main 现为两轮返修后的版本。
- **未做、未生效**：批 1–5 没拆单、没写代码；合并只代表规则成立，落地验收（L3）和常浩签托管策略之前，猛鬼兽不按托管行事。常浩日常会话属全权会话，批 3（受限执行身份）之前托管送不出任何决定。
- **托管线下一步**：按规格第 7 节把批 1–5 拆成 #126 的子 issue（落地工单按 L3），开工前先给常浩分批计划。项目主线的唯一下一步不变，仍是日报 2b（见下一条）。

## 2026-11-09 · 日报 2a 写稿与核对已合并（#111；PR #125 `21a9edb`）

- **内容**：
  - 新增 `hermes/scripts/pdaily_publish.py` 和写稿提示词 `hermes/prompts/pdaily_report.md`。
  - 流程：读 facts.json，压缩后用 `hermes chat` 单次让 Hermes 写卡片要素和完整报告，脚本先规整再核对，最后按甲版样例出卡片 JSON。
  - 核对分两类：
    - 编造类，不通过就出「分析降级」事实版：编号、百分比、PR 数、「已安装 / 已验收」说法、实质推进、三个数、七段。
    - 写法问题，自动修并记提醒：最晚时间格式或出处、超长、回复外壳。
  - 事实版也出错时出「日报没出成」最小卡片；取数失败同样出这张卡片。
  - `ai_access.json` 和 AGENTS 的「AI 接入」一节登记了 `pdaily`（仅支持 Hermes）。
- **完整报告放哪（常浩 11-09 定）**：飞书云盘「我的文件夹 > 个人」（folder token `MKmvfAZSvlby0sdDzwIcrXVZn2g`），每天一份，仅本人可见，2b 照此建。
- **真实写稿**：Hermes（gpt-6.1-sol）用 11-08、11-07 真实快照各写一稿，都一次写成。11-07 那稿被「今日推进写简称」误拦一处，已修。按最终规则复核三份稿，零误拦。完整报告每份约 1.8–2 万字，长不长等 2c 试跑时常浩看。证据在 `evidence/111-2a/`。
- **审查**：只读子 agent 两轮。第一轮 3 个 P0、15 个 P1，第二轮 3 个 P1，都已修。剩下的 P2 和已知缺口写在 PR 里：编号和项目是否对应不核、报告正文活动量数字不核、「今早新增」没有取数来源、最小卡片不写 build.json。
- **测试**：`aa4e2fd` 上 hermes 684 项、orca 48 项 OK；`test_pdaily_publish` 71 项，16 处改坏全部抓到。CI 通过后带 `--match-head-commit` squash 合并。
- **未做**：不发送、不建云文档、不安装、不装定时任务。旧的 07:30 日报（`33df73dffd17`）照常跑，归 #112。
- **唯一下一步**：做 2b：用常浩本人身份在「个人」文件夹建当天完整报告并回读，猛鬼兽 bot 发卡片到私聊、按钮指向文档，B 层存档，`actions.record()` 记发送；用 11-08 数据试发一次。开工前先列计划。

## 2026-11-09 · 日报 1c 事实快照已合并（#110；PR #124 `91670c9`）

- **内容**：新增 `hermes/scripts/pdaily_facts.py`，把 1a 的项目记录和 1b 的行动合成 `DigitalTwin/daily/<统计日>.facts.json`。快照里有：
  - 拍板候选六类：合并官问常浩、写入单等确认或回执要决定、最近一期没回复的待决提案、auth_gap、夜跑受阻、CURRENT 点名常浩（过期的不列）；
  - 风险四类：交付风险取不到，写进覆盖；
  - 跨项目明文引用：会查被引用的那张关没关；
  - 观察池：新坑只看统计日内；失败、合并官不一致往回扫 14 天现算；
  - 覆盖与可信度：取不到写 null，不当 0；某一段出错只记这一段「未知」，其余照出。
- **1a 改动**：1a 的合并 PR 记录加了 `ref_lines`：标题和正文里写了「名字#编号」的原句。审查发现 1a 原来不输出正文，跨项目引用只能扫到标题。AGENTS 结构表加一行。不安装、不发消息、不装定时任务。
- **常浩 11-09 定的三条**按原样落实：只给候选和出处，不定级、不排序、不推荐；Hermes 对话记录不接；观察池不存状态。
- **审查**：只读子 agent 四轮。前三轮的 P0 1 条（已回复的提案仍被当成待决）、P1 7 条都已修复；第四轮是 0 P0、0 P1，剩的 1 条 P2 也已修复。
- **测试**：`3d2020c` 上 hermes 613 项 OK，orca 48 项 OK。`test_pdaily_facts` 37 项，反向改坏 11 轮、84 种都抓得出，每项测试都至少被一种改坏打红过。
- **回放**：11-07、11-08 真实数据回放，退出码 0，没有报错。11-08 逐条对照 V2 10.1 样稿：夜跑 SBX-24、PR #102、主控定时任务 auth_gap、my-skills#74 → niuma-home#31 没关、79 处未提交（待识别）等都能找到；差异写在 PR 里。证据在 `evidence/110-1c/`，对照表是 `replay-compare.md`。
- **已合并**：常浩说「合并」后，把最新 main 并进分支（只多了 CURRENT.md，代码和跑过全量测试的 `3d2020c` 一样），CI 通过后带 `--match-head-commit e35d803` squash 合并为 `91670c9`。主目录已拉齐，worktree 和分支已删。`pdaily_facts.py` 没有安装，也没有接定时任务。#110 批 1 的 1a、1b、1c 全部合并。
- **唯一下一步**：做 #111：Hermes 读 `facts.json` 写飞书卡片和完整报告、发送。开工前先列分批计划。

## 2026-11-09 · 日报 1b 行动记录已合并（#110；PR #123 `ccf0022`）

- **内容**：新增 `hermes/scripts/actions.py`。按 V2 5.4 定下 `actions.jsonl` 的结构，提供 `record()` 记录函数；`--date` 按统计日列出代理行动：`actions.jsonl` 加六个来源现读现转（合并官、merge-runs、派单台账、定时任务库、夜跑、裁决文件），每条带授权档位和来源，低于应有档位的标 `auth_gap`。AGENTS 结构表加一行。不安装、不发消息、不装定时任务，还没有脚本调用 `record()`。
- **常浩 11-09 按推荐定的三点**：历史不迁移，现读现转；裁决文件只摘原文，不判断批准还是拒绝；安装只认 `record()` 写的记录。另有三处口径我按推荐定了，写在模块说明里：
  - C 档文件逐项写明的 D 档动作，记 D 档；
  - 夜跑的执行者不拿批准人顶替；
  - 写入单开单一律记「升级给常浩」。
- **审查**：只读子 agent 五轮。前四轮的 P0、P1 都修了，第五轮是 0 P0、0 P1，剩 4 条 P2，修了 3 条，1 条写进遗留（以后 `record()` 写进来的档位口径）。
- **测试**：`7276218` 上 hermes 576 项 OK，orca 48 项 OK；`test_actions` 28 项，反向改坏 6 轮 43 种都抓得出。真实数据回放了 10-26、10-27、10-29、11-07、11-08、11-09，七个来源都取到。回放里查出的真实信号：新建的 3 个定时任务、10-26 的 9 张写入单都标了 `auth_gap`，需要对照授权核对。证据在 `evidence/110-1b/`。
- **已合并**：常浩说「合并」后，把最新 main 并进分支（只多了 CURRENT.md，代码和跑过全量测试的 `7276218` 一样），CI 通过后带 `--match-head-commit 85f4079` squash 合并为 `ccf0022`。主目录已拉齐，worktree 和分支已删。`actions.py` 没有安装，也没有脚本调用 `record()`。
- **唯一下一步**：做 #110 1c（汇总与回放）：拍板候选、风险四类、跨项目引用、观察池、覆盖与可信度；把 1a 的项目记录和 1b 的行动接起来，写 `DigitalTwin/daily/<统计日>.facts.json`；用 11-07、11-08 的真实数据回放，对照 V2 样稿。开工前先列分批计划。

## 2026-11-09 · 日报 1a 取数底座已合并（#110；PR #122 `5d4036b`）

- **判定方向**：常浩 11-09 定「甲：只摘原文」：合并 PR 的工程记「已合并」，交付、验收一律「未核实」，只附当天 CURRENT 对上的节和 PR 标题里的自述原句，不下结论；判定等 1b 的行动记录和回读证据。
- **内容**：新增 `hermes/scripts/pdaily_data.py`（只读、标准库、JSON 输出）；改 `progress_data.py` 的下一步抽取（F6 也用）；AGENTS 结构表加一行。不安装、不发消息、不装定时任务。
- **审查**：只读子 agent 共八轮。第七轮的 1 个 P1「分支上写好、晚合并的节在哪天都不出现」已修：当天的节改为统计日内新进主分支的节，标题日期最多往前认 7 天。第八轮复核结果是 0 个 P0、0 个 P1、4 个 P2，P2 写进 PR 的「遗留」。
- **测试**：`378800e` 上 hermes 548 项 OK，orca 48 项 OK；新增测试都做了反向改坏验证。真实数据回放 11-07～11-09（trainee-hub、postpane、disk-slim，外加 11-08 全名册 16 个项目），全部取到数、无报错。证据在 `evidence/110-1a/`。
- **已合并**：常浩说「合并」后，先把最新 main 并进分支（未强推），重跑测试：hermes 548 项、orca 48 项 OK。CI 通过后，带 `--match-head-commit 22a8d23` squash 合并为 `5d4036b`。主目录已拉齐，worktree 和本地分支已删除，远端分支已自动删除。#110 保持打开，1b、1c 还没做。`pdaily_data.py` 没有安装，也没有接定时任务。
- **唯一下一步**：做 #110 1b（行动记录 `actions.py`，交付和验收在这一步接入安装记录和回读证据），之后做 1c，再进入 #111 写稿与发送。

## 2026-11-09 · 夜间行动提案立项（PRD D23，#117；规格 PR #121 已合并 `8340039`）

- **来由**：常浩要每晚分析各项目进度、找机会点、提出下一步建议，由他决定当晚要不要让猛鬼兽接手推进。按推荐定了三点：22:30 发、00:30 前没回不做；夜里只做到开 PR；一晚最多 3 单、每单 ≤2 小时、同一时间 1 单。
- **授权**：常浩回「做 1 3」或「按推荐」，就是对这几单的一次 D 档本人授权，猛鬼兽原样记成当晚的单次授权文件，不用改授权清单。
- **issue**：#117 父；#118 批 1 提案生成（等 #110）；#119 批 2 夜间执行 + 日报「昨夜代理执行」（等 #118、#111）；#120 批 3 定时上线（D 档）。
- **卡片**：三版（甲·清单 / 乙·一张表 / 丙·逐条折叠）用真实 `ready-for-agent` issue 发私聊实测，常浩选**甲·清单**，样例 `hermes/samples/night-proposal-card-2026-11-09.json`。证据在 `evidence/night-proposal-mock-1109/`。
- **另**：日报卡片改为默认宽度，PR #116 已合并（`5e90805`）。
- **唯一下一步**：日报 1a 在它自己的会话里等常浩选判定方向；1a 合并后接着 1b、1c，再做 #111 和夜间提案 #118。

## 2026-11-09 · 日报形式定为飞书卡片（甲版·仪表盘）+ 完整报告云文档（#111，PR #115 `2096cb8`）

- **来由**：常浩不要 1200–2000 字的文字简报，要参考图那样的飞书卡片：提炼前一天的项目情况，想看细节就点进完整报告。
- **实测**：用 11-08 模拟数据做了甲（仪表盘）、乙（行动优先）、丙（数字 + 折叠）三版，猛鬼兽 bot 身份发到常浩私聊，在飞书 Mac 客户端逐张看过（表格、折叠、按钮跳转都正常）；模拟完整报告建在常浩云盘（路觅科技企业空间，仅本人可见）。常浩选**甲**。证据在 `evidence/pdaily-card-mock-1109/`。
- **PR #115**：规格第 3 节（双层、篇幅、留痕）、新增 4.0 卡片结构、4.1–4.8 改为完整报告结构；PRD D22 同步；甲版样例 `hermes/samples/pdaily-card-2026-11-08.json`。纯文档，CI 通过，常浩说「合并」后 squash 合并。决定已记到 #111 评论。
- **待定**：完整报告云文档放哪（现在是常浩云盘，路觅科技企业空间），#111 上线前请常浩定。
- **#110 1a**：会话「日报 1a 取数底座：修第二轮审查并开 PR」推送到 `2672e7b`，两套全量测试通过；第三轮审查仍有 3 个 P0、5 个 P1，都出在从 CURRENT 文字判「已安装 / 已通过」，等常浩在那个会话里选方向（推荐甲：只摘原文、标「未核实」，判定交给 1b 的安装记录）。PR 未开。
- **唯一下一步**：常浩在 1a 会话里选判定方向；1a 合并后做 1b（actions.py）、1c，再进 #111 写稿与发送。

## 2026-11-08 · 个人项目日报立项：定义改为代理人、常设授权清单 v0、日报规格 V2（#108；批 0 #109 PR #113 已合并 `5190c48`）

- **来由**：常浩要猛鬼兽每天发个人项目日报（推进了什么、完成了什么、待完成、紧急、重点、建议沉淀成规则或 skill 的经验），并重新定义数字分身：个人项目的第二分身，不在场、不逐件批准也能推进。常浩定：先不管三报（D20 不动）；不放飞书工作事项（归亚古兽）；定义写进 PRD，授权清单按草案。
- **审核**：V1 设计稿经 ChatGPT 审核（《个人项目日报体系深度分析与 V2 优化方案》），结论是方向对，但先做产品层修订。Claude 逐条核对后：大部分采纳；调整 3 处（开头不放活动量；目标和里程碑补上数据来源；跨项目依赖只认明文引用）。常浩「按推荐，开改」。
- **issue**：
  - #108 父；
  - #109 批 0（PRD 与授权清单）；
  - #114 批 1 前置（16 个项目补「当前阶段目标」，Hermes 起草、常浩确认）；
  - #110 批 1（取数脚本，行动记录结构在本批定稿）；
  - #111 批 2（A、B 双层生成与投递，试跑 3 天，内容质量四问）；
  - #112 批 3（08:00 定时任务、四层运行状态、旧 07:30 任务处置，D 档要授权）。
- **PR #113**（分支 `claude/109-twin-definition`，`1599aaa`、`ec0e5bc`）：
  - PRD v1.2：原则 7、D1、D6、F5.3，新增 D21、D22；
  - `docs/常设授权清单.md` v0，第 5 节有三处说明；
  - `docs/个人项目日报-V2-设计.md`（含统计日 11-08 真实数据样稿，A 层约 2000 字）；
  - HERMES.md 第 4 节加「常设授权」。
  - 测试：hermes 490 项 OK，orca 48 项 OK。
- **没做**：HERMES.md 没安装、网关没重启（D 档，合并后单独授权）。
- **范围外发现**：
  - M5 夜跑 22:19 因 SBX-24 被标 blocked 停机（STOPPED），主控定时任务 6fafbfe09d8d 仍 active；Hermes 20:04 以「Claude 转述、核验不了」回复受阻。release 和暂停主控需要常浩在飞书私聊亲口交代，或自己处理；授权 72 在 11-09 06:00 失效。
  - progress_data 在牛马之家、GitHub 快速中心抽「下一步」抽错，放进 #110 一起修。
- **已合并**：常浩说「合并」后，分支并入最新 main（未强推），重跑测试 hermes 490 项、orca 48 项 OK，CI 通过，带 `--match-head-commit` squash 合并为 `5190c48`，#109 关闭；主目录已拉齐，worktree 已删。
- **已安装（11-08 22:56，常浩说「安装并重启」）**：
  - 旧版备份、sha256 和网关日志在 `evidence/2026-11-08-HERMES常设授权-安装/`；装前核对旧安装副本等于合并前的 main。
  - 注入扫描用 Hermes 的 `tools/threat_patterns.py`，context、strict 两种模式都是 0 命中。
  - `~/.hermes/workspace/HERMES.md`（600，`cb771945…`）和 main `cmp` 一致。
  - 网关 `launchctl kickstart -k`：新实例 22:56:30 起来时，旧进程 39740 还没退干净，报「A gateway already owns this host」后以退出码 1 退出；launchd 22:57:01 自动拉起，22:57:04 `feishu connected`，新 PID 86812。猛鬼兽断线约 36 秒。
  - 飞书里已有的会话要 `/new` 后才加载新规程。
- **#114 已完成（11-08 23:14，常浩「全部按推荐」）**：16 个项目都写上了「当前阶段目标」一行（11 条明确目标、3 条常态维护、2 条暂停；agent-kit、my-skills、carvomoto 写在 README），回读 origin/main 16/16，提交号见 #114 关闭评论。digiworld 主目录拉齐失败（本来就落后，未跟踪的 `项目关系-定稿.html` 挡住了更早的提交），没动。起草时发现日报规格 5.3 两处要改（`- [ ]` 清单多是验收条件不是父 issue；三个项目没有 CURRENT），记在 #110。
- **#110 1a 进行中**：分批计划常浩已确认（3 个 PR，计划在 #110 评论）。1a 在分支 `claude/110-pdaily-data`，提交 `68ecf1d`，已推送、未开 PR，worktree 在 `.claude/worktrees/110-pdaily-data`。独立审查给了 1 个 P0、5 个 P1，清单在 #110 评论，还没改。
- **唯一下一步**：在 worktree 里按 #110 的审查清单修完 P0、P1，补反例测试，跑全量测试，再审一轮后开 1a 的 PR。

## 2026-11-08 · Tidings 周报横幅换新图（#106，PR #107 `659fafd`，配合 github-center #86）

- 改了什么：`hermes/scripts/assets/tidings-banner.png` 换成常浩用 GPT 生成的深色横条（1901×487，从 2000×667 原图抠出圆角线框内部分；副标题改成「近 7 天新增 Star · TOP10」）；插图从 `crop_center` 改为 `fit_horizontal`，按原比例完整显示。github-center 的卡片同时去掉橙色标题栏，横幅就是标题。
- 验证：全量 490 项通过；新卡片多次经猛鬼兽身份实发到私聊（临时状态文件，不动正式已发记录），飞书电脑端截图核对，整张卡约为改前的 70%。手机端未实测。
- **已安装（11-08 22:12，常浩说「合并并安装」）**：`~/.hermes/scripts/tidings_weekly.py` 和 `assets/tidings-banner.png` 换成 main 版，`cmp` 一致，`--dry-run` 输出「不发：还没到周一 19:30。」；旧版和校验值在 `~/Library/Application Support/DigitalTwin/evidence/2026-11-08-tidings-banner-安装/`。新横幅第一次正式发送时会按内容哈希自动重传。
- **下一步**：11-09（周一）19:30 后看私聊收到的周报卡片是新版（横幅、标签、按钮），核对后本条结束。

## 2026-11-08 · Tidings 周报默认周一 19:30、不早于每日更新（#104，PR #105 `424dc18`，配合 github-center #83）

- 改了什么：`tidings_weekly.py` 默认周报时间 11:30 → 19:30；按 github-center `readSettings` 同一规则算周报时间（每日更新早于 17:00 或不合规按 18:00，周报早于每日更新按每日更新后 1.5 小时，最晚 23:45），因为本机 `data/settings.json` 是从服务器原样拉回的，可能还是旧的 11:30。
- 验证：`python3 -B -m unittest discover -s hermes/scripts/tests` 490 项 OK；周报推送 20 项（新增迁移用例）。
- **已安装（11-08 18:49，常浩说「装新版并重启」）**：`~/.hermes/scripts/tidings_weekly.py` 从 main 复制，`cmp` 一致；`--dry-run` 输出「不发：还没到周一 19:30。」；旧版备份和校验值在 `evidence/2026-11-08-tidings-1930-安装/`。定时任务 `18a21b2eb87a` 本来就是每 15 分钟一次，不用改。
- **下一步**：11-09 19:30 后看私聊是否收到统计日 11-08 的周报卡片（github-center 当天 18:00 出榜）。

## 2026-11-08 · M5 第 1 批修补复验：演练 5（场景 1）、演练 4（场景 2）都通过并 release（#97）

- **来由**：上一节的「唯一下一步」。常浩飞书修正授权（消息 3142，`hermes-49`）第 3 条：复验脚本先给 Hermes 审过、批了，再依次跑演练 5、演练 4。
- **复验脚本**：v3–v6 四版都被 Hermes 指出漏洞，照着返修：时间窗口（`hermes-50/50b`）、基准归属和慢样本（`55b`）、G2b 与 stop 之间隔着 PRE 查询（`58`）、G2b/G2c 没绑定同一个 run（`63`）。v7（`claude-64`，sha1 9b2a7bd1）在 G2 时保存目标 run 的完整 id，G2b 精确核对 running、G2c 精确核对 cancelled。自测 `claude-65` 14 个场景：3 个通过且调用 release，11 个失败且没调 release。Hermes 复审 v7（申请 `claude-66`，裁决 `hermes-66`）批准 v7 用于演练 5 → 4，并写明不是开跑放行。
- **演练 5（`claude-67-复验演练-5.log`，drill-5，SBX-18）**：
  - 目标 run 01a1bac8：stop 前是 running，stop 后是 cancelled；
  - 停机时没有残留进程；
  - G5 9 次探针都干净（第一次 +0.18 秒，最大间隔 1.69 秒，最后一次 +10.84 秒）；
  - G6 7 次远端快照和 stop 前一致；
  - G1–G8 全过，release 退出码 0，state.released 已写入。
- **演练 4（`claude-68-复验演练-4.log`，drill-4，SBX-20）**：
  - G3 看到 agent 放到后台的 `python -c time.sleep(900)`（另有一个 `tail --pid` 等它）；
  - 目标 run 01a1bacd：stop 前是 running，stop 后是 cancelled；
  - 主控报告「停机时残留 3 个，结束后剩 0 个」，这正是第 1 批要修的场景；
  - G5 10 次探针都干净（第一次 +0.01 秒，最大间隔 1.22 秒，最后一次 +10.25 秒）；
  - G6 7 次一致；
  - G1–G8 全过，release 退出码 0，state.released 已写入。
- **执行边界**：每场跑之前都核对了 v7 和主控（2c948decda37）的 sha1，没有带自测用的覆盖变量；两场都没有冻结；定时任务 6fafbfe09d8d 仍暂停；没碰 drill-3。
- **测试**：本节没有改代码，复验证据就是上面两份日志；本 PR 的测试见上一节。
- **本轮新坑**：无。
- **放行申请（申请 `claude-69`，裁决 `hermes-69`）**：Hermes 判定第 1 批修补和两场复验满足技术放行前提，但现有授权覆盖不了下一次开跑（`hermes-34` 第 3 条「开跑另行申请」；08 号已被取代；09 号 12:30 的窗口已过；66 号只批脚本）。PR #102、第 3 批不算硬前提，但下一次运行实际依赖的那些例外（方案甲偏离架构 7.3、用本地登录凭据、`executor.kill`、release 权限、自动交接）要常浩本人逐项写明。它列出常浩要签发的 7 项内容：运行对象和时间窗、允许哪些真实动作（approve、start、恢复定时任务）、边界和上限、清理与冻结、例外和剩余风险、release 与交接、放行委托边界。
- **状态变化**：PR #101 已在 11-08 13:34 合并（e95bb4a，只改 ARCHITECTURE.md），之前的交接写的「等常浩定」已过时。
- **开跑授权与准备（11-08 19:25–20:04）**：
  - Claude 起草计划 `claude-70`（就是 main 的 `hermes/samples/multica-night-2026-11-08.json` 原样，sha1 e564e7bb；todo-lite v0.2 四个 stage，首夜那晚没跑过）和授权草稿 `claude-71`。常浩在飞书私聊发给 Hermes（`hermes-72`）；Hermes 核过是本人消息（`hermes-73`，飞书消息 om_x100b60324f1d0cb4de39f4389eed32f，19:42:21）。
  - 开跑前清单 `claude-73`：
    - main 1d17de0 hermes `Ran 490 tests` `OK`；
    - orca 首跑 `FAILED (failures=1)`，失败用例名没截到，补跑两次 `OK`，记作未闭环异常，不是本执行单的门禁；
    - 工作区 11 张演练遗留的非终态单不阻断，不清理。
  - Hermes 第一次没放行（`hermes-73`），要执行器现场的原始回读；补交 `claude-74a`（容器 `true unless-stopped`，`executor_procs(plan,"list")` 返回 `[]`）后批准三步（`hermes-74`）。
  - 三步都已完成并回读（`claude-75`）：
    - `plan` 建了父单 SBX-21 和子单 SBX-22～26；
    - `approve` 22:00 开跑，上限取计划里的 4 小时、20 次；
    - 定时任务 6fafbfe09d8d 恢复为 active，20:01:51 第一次 tick 正常，没有提前开跑。
- **未做**：夜跑本身（22:00 由 tick 自动开跑，最晚 02:30 停机、03:00 停净）；第 3 批（`claude-31`，等常浩批）；Multica 上游 issue（`claude-29`，未发）；本 PR 的合并；驿窗那组 1、2、3、18 号；orca 首跑失败的原因。
- **唯一下一步**：今晚夜跑由定时任务推进，有事会发到常浩的飞书私聊；正常 STOPPED、回读全满足时由 Hermes release，并把定时任务改回暂停。明早常浩看夜跑报告和 dt-sandbox 的 PR，决定送审和合并。

## 2026-11-08 · M5 演练问题修补：第 1 批已合并安装，复验演练 3 门槛设计有误、等常浩修正授权（#97、#99）

- **来由**：M5 首夜两次演练后 Hermes 两次不放行（Multica 显示 STOPPED 不等于执行器已停；取消 run 杀不掉 agent 放到后台的进程）。常浩要求「基于这次演练暴露出的问题，请列一个问题清单，然后逐个解决」。清单 29 条、分批计划见证据目录 `2026-11-08-M5首夜/` 00、24 号。
- **第 1 批（PR #100，#97，L3，已合并 a3e497d）**：停机在 Multica 回读之外，再到执行器容器里独立核对进程，为空才写 STOPPED；`executor.kill: true` 时结束残留，夜跑途中每次 tick 清孤儿；停机后到 release 前，残留结束不掉或读不清就撤销 STOPPED、冻结；停机后给远端拍快照、持续比对；消息文件尾部截断记为缺口。原计划 27②（改执行器 runtime 配置）不做，改由主控三处清理。
- **审批**：常浩飞书授权（`hermes-34`，到 11-10 00:00）。Hermes 第 1 次（`hermes-35`）指出停机后只告警不冻结、复验脚本门槛不硬，不批；返修后第 2 次（`hermes-40`）批准合并，条件是 CI 成功；复验脚本又修三处后，第 3 次（`hermes-43`）批准复验。
- **测试**：19 项新用例先红后绿；全量 `/usr/bin/python3 -B -m unittest discover -s hermes/scripts/tests` `Ran 489 tests` `OK`；`orca/tests` `Ran 48 tests`：首跑 1 项计时用例失败，补跑 `OK`（main 上同样不稳，本 PR 没改 orca）；CI `test: SUCCESS`。
- **安装**：`~/.hermes/scripts/multica_night.py` 与 main 的 cmp 一致（sha1 2c948decda37），旧版备份在 `claude-42-安装备份/`；定时任务 6fafbfe09d8d 仍暂停。
- **复验演练 3（`claude-44`、`claude-45`）**：主控全部正常（STOPPED，没有残留，10 次探针都干净，停机后远端没变）。但没通过，原因是我设计的门槛有误：G6 拿演练前的快照比，agent 在 stop 前正常推的 PR #8 被判失败；场景 1 的 run 在 stop 前已经结束。按边界不 release，演练 4 没跑。脚本 v3（`claude-46`，自测 `claude-47`）和修正授权草稿（`claude-48`）已备好。
- **第 2 批（本 PR，#99）**：名册里 digital-twin 的测试命令钉死 `/usr/bin/python3`；AGENTS 写明测试版本和证据文件写入方前缀；tests/README 不写死总数；新增 `test_roster.py`。#14 架构 5.3a 修订见 PR #101（等常浩定）。
- **未做**：复验演练 4、5；第 3 批（决策清单 `claude-31`，等常浩批）；Multica 上游 issue（草稿 `claude-29`，未发）；驿窗那组 1、2、3、18 号。
- **唯一下一步**：常浩核对 `claude-48-修正授权草稿.md`，在飞书发给 Hermes；Hermes 审过脚本 v3 后，Claude release drill-3，再跑演练 5、演练 4。

## 2026-11-08 · M5 首夜：Hermes 主控的 Multica 夜跑与独立停机检查（#92）

- **来由**：常浩要今晚做 Hermes 主导的 Multica 无人值守测试，11-08 选「方案甲」（Hermes 外部推进，父单指派给常浩）。约 02:00 常浩说「我要去睡了，剩下所有的需要批准的内容都让 Hermes 去批准。」
- **改了什么**：新增 `hermes/scripts/multica_night.py`（plan / start / tick / stop / status / report / release）：按计划建单、按 stage 推进、边跑边导 run-log 和消息、到上限按 5.3a 停机、回读不过就冻结执行器容器，计数持久化、重启不清零；`tests/fake_multica.py` + `test_multica_night.py` 68 项；`multica_night_tick.py` 是定时任务入口；`approve` 记下几点开跑，到点由 tick 开跑；名册 v1.6 给 dt-sandbox 标 `sandbox: true` 和 Multica 工作区；今晚计划 `hermes/samples/multica-night-2026-11-08.json`；执行单 `docs/DT-ORC/migration/2026-11-08-M5-首夜执行单.md`（写明和架构 7.3 等处的差异、批准来源、开跑前清单、时间线、验收）。
- **测试**：`python3 -B -m unittest discover -s hermes/scripts/tests` → `Ran 464 tests`（系统 Python 3.9，和 Hermes、CI 同版本） / `OK`；`orca/tests` → `OK`。真实 multica 只读冒烟：issue get / runs / wakeup list / autopilot list / run-messages --since / issue list --updated-since 的拼法和输出格式都对。
- **Hermes 审批（02:48 左右）**：按常浩委托把 7 项（合并、安装、定时任务、清理 SBX-2/3、令牌偏离、真实演练、22:00 开跑）交 Hermes，**7 项全部不批准**：委托经 Claude 转述、未经常浩本人在已核验私聊或亲手操作确认，不能当作授权，也不得记为 owner_approved。请求与原样回复在 `evidence/2026-11-08-M5首夜/`。
- **独立审查**：子 agent 只读审查出 10 条，第 1–9 条已修并补测试（见第三个提交），第 10 条的测试缺口已补；「取消 running 的 run 实际要多久」留到真实演练量。
- **Hermes 飞书批复（08:34，`03-Hermes飞书批复.md`）**：常浩在飞书私聊委托后，Hermes 附条件批准第 2–7 项，第 1 项（合并）要先有绑定 HEAD 的独立技术审核。已执行第 4 项（SBX-2、SBX-3 改 cancelled 并回读，`04-清理探针单.md`），核验了第 5 项的隔离前提（`05-隔离前提核验.md`）。
- **ChatGPT High 独立审查（驿窗 `h1a1b894e96600`，L3）**：第 1 轮「需修改」（P0 ×1、P1 ×7、P2 ×3），已全部返修并补测试；第 2 轮「需修改」、出口决策升级（剩 P0 ×1、P1 ×2、P2 ×1），常浩选「修完再复审」，已返修并送第 3 轮（只看这 4 处）。P1-07（TTY + 绑定确认只防误操作、不是身份证明）的剩余风险待常浩确认。第 3 轮（驿窗 `h1a1b8d23ccb01`，因本会话 MCP 旧版改由无头 claude 代发）仍「需修改」：最终导出跳过已记账 run（P0）、advance 检查与改状态之间的竞态（P1）、停机途中出现应急标记仍写 STOPPED 且 release 不查标记（P1）、空行不算缺口（P2）；代码已修（提交 76012ad）。反向测试已补 6 项（`test_r3_*`；`fake_multica.py` 的 spawn 可在某条命令第 n 次调用时写文件，模拟应急标记恰在 advance 读单后、停机回读中出现）：换回修复前的主控 6 项全失败，逐处单独撤回修复也各有专门的测试失败。满 3 轮后常浩签发常设授权（`08-常设授权.md`）：不要第 4 轮外审，由 Hermes 批合并；P1-07 剩余风险常浩已接受。
- **11-08 上午（常浩 10:00 改为「准备好就开跑，12:30 绝对截止」，Hermes 记为 `09-开跑窗口变更.md`）**：
  - Hermes 12 号裁决附条件批准合并、安装、定时任务、演练、开跑；PR #93 squash 合并为 aee05e5（HEAD a67a4c4，CI pass，全量 470 + orca 48 OK）；两个脚本装进 `~/.hermes/scripts/` 并 cmp 一致；建定时任务 6fafbfe09d8d（每 5 分钟、无模型、投递常浩私聊），10:13 首次 tick 准点、静默。
  - 演练 1（SBX-11/12）：running 后约 23 秒 stop，1 秒回读全过、STOPPED；但 STOPPED 时容器里 claude 进程还在，取消同一秒 dt-sandbox 打开 PR #7。Hermes 16 号不放行，要求「数分钟后」停并核验进程退出与外部写入。
  - 演练 2（SBX-13/14）：运行约 4 分钟后 stop，回读全过、STOPPED，远端 3 分钟以上无任何写入；但 **agent 放到后台的进程没被取消杀掉**，在容器里又活了约 11.5 分钟。Hermes 22 号再次不放行，19 号兜底脚本（残留 120 秒就停容器）也不批。
  - **结论：本轮没有开跑。** 定时任务已按 03 第 3 项暂停（没删），两个演练夜跑都已 release，容器里只剩守护进程。证据 `evidence/2026-11-08-M5首夜/` 11–23 号；问题清单 `00-问题与冲突清单.md` 共 29 条，顶部按仓库分了组。
- **未做**：正式夜跑（建单、开跑、晨报）。
- **范围外发现**：`hermes/scripts/tests/README.md` 里的总数「共 356 项」早已过时（合并前就是 402），没改。
- **已知缺口（没改代码）**：① 停机回读只看 Multica 状态，不查执行器容器里有没有残留进程，也杀不掉 agent 放到后台的进程（清单 25、27）；② 已记账 run 的消息文件尾部被截掉时查不出来（清单 21，Hermes 12 号按已披露剩余风险接受）。
- **唯一下一步**：常浩看 `00-问题与冲突清单.md` 顶部的分组，决定要不要开单补「停机时核验并清掉执行器里的残留进程」（主控代码 + 测试 + 真实演练，冻结 / 杀进程的授权范围写清）；补好之前不再开跑。

## 2026-11-08 · DT-ORC ARCHITECTURE 对齐 MA-02 审查预算（#94）

- **来由**：multi-agent MA-02 工程审查契约 v0.2（`00131dc`）。常浩 11-07 批准审查预算原则，11-08 在 #94 上回复「照单里改」；驿窗已落地（postpane#233，`fb62a6a`）。
- **改了什么**：`docs/DT-ORC/ARCHITECTURE.md`
  - 第 0 节第 2 条：不再写「最多 2 轮」；
  - 3.3：工具参数加 `risk?`、`extra?`；「自动往返最多 2 轮」一条改为按风险级的预算（L1 1 轮收口、L2/L3 最多 2 轮、L0 拒收、同一 PR 跨会话累计、超预算授权续审、回信倒数第二行审查出口）；现行做法里写明 `risk` 填任务的 L 级；以后建审查员 agent 也要带 `risk`；
  - 6.4：第一条按风险级改；「HEAD 变化旧结论失效」补「轮数不清零」；超限一条补 `extra`。
- **派单代码没改**：查过 `hermes/scripts/dispatch.py`、`run_worker.py`，现在没有 worker 调用 `relay_to_chatgpt`。3.3 写明审查员 agent 还没建，送审由常浩桌面上的 Claude 会话发起，所以本单只改文档。以后建审查员 agent 时，再把 `risk` 写进它的契约。
- **测试**：见 PR。
- **唯一下一步**：沿用上一节（批 2 与 I-DW-1）。

## 2026-11-07 · AI 接入按全局规则 P-13 补齐：配置文件、自检命令、不可用报错不换家、留痕

- **来由**：常浩 11-07 新定全局规则 P-13（AI 接入写进设置、讲清来源、能切换）；母会话分项目补齐，本项目没有图形界面，按例外用配置文件 + 自检命令。盘点后常浩「按推荐开工」，三项决定：Hermes 回退 deepseek 保留并登记为例外；车间只设一个默认执行方（出厂 Codex），点名优先；常浩的选择存在 DigitalTwin/ai-access.json，不改仓库文件。
- **改了什么**：新增 `ai_access.json`（功能清单、执行方、例外、对外开放、数据对象）和 `hermes/scripts/ai_access.py`（status / check / set）；`dispatch.py new` 的 `--tool` 可不填，按配置选，台账记 `tool_source`；`run_worker.py` 开跑前查执行方（`claude auth status` / `codex login status`），不能用就写 refused 回执、退出码 4，不换家；回执加 `executor`、`access`、`model`、`chosen_by`，状态库一并保留；`report_publish.py` 在日报 / 周报标题下一行写「生成：Hermes（实际模型）｜接入：Hermes 派单｜时间」；`self_check.py` 每天查车间默认执行方，不能用才说；workshop 技能改为没点名不带 `--tool`、回报第二行写留痕、不可用不换家；AGENTS.md 加「AI 接入」一节。
- **实测**：隔离派单目录（真实 Claude / Codex、真实名册，选择文件也在证据目录）：出厂默认开单 → codex；`set default claude` 后开单 → claude，跑完回执 `Claude Code · 本机 CLI · claude-opus-5-5 · AI 接入默认执行方（常浩切换过）`。记录在 `evidence/2026-11-07-AI接入实测/`。
- **测试**：见 PR。
- **已合并、已安装（11-08 00:39）**：常浩点「合并并安装」。PR #89 CI 通过后 squash 合并为 `1b30bcc`，主目录 `pull --ff-only`；`ai_access.py`、`dispatch.py`、`run_worker.py`、`report_publish.py`、`twin_state.py`、`self_check.py` 复制到 `~/.hermes/scripts/`，workshop 技能复制到安装位置，`cmp` 一致；装前核对旧副本都等于合并前的 main，备份在 `evidence/2026-11-08-AI接入安装/`。网关用 `launchctl kickstart -k` 重启（PID 86840 → 39597），飞书第一次连接超时，重试后 `running connected`；workshop 为 enabled。在安装位置跑 `ai_access.py status`：默认 Codex（出厂），Claude、Codex、Hermes 都可用。
- **范围外，已处理**：安装后的工程自检报 `progress` 技能安装副本和 main 不同：Hermes 11-05 后台复盘时自己往安装副本里加了「个人当日回顾与技能成果问法」一节，没回写仓库。常浩 11-08 选「收进仓库」，#90 把安装副本原样收进 `hermes/skills/progress/SKILL.md`（安装副本本来就是这份，不用重装、不重启网关）。
- **唯一下一步**：常浩看 #90 的 PR，回复「合并」。

## 2026-11-07 · 名册 v1.5：加 type，登记 multi-agent、ui-system（#84）

- **来由**：常浩转来《项目体系重构 V1.3》定稿并要求按批次推进；本单是执行单 I-DT-1。决定记录、V1.2/V1.3 原文与执行单已放进 `docs/项目体系重构/`（I-DT-2，`1211b4e`，直接进 main）。
- **projects.json v1.5**：16 个活跃（product 10、capability 3、infrastructure 3），每条加 `type`，顶层加 `types` 说明；`category` 等原字段、inventory 都没动（机器逐条比对 v1.4 一致）。
- **两个新能力项目**：`multi-agent`（`b49082e`）、`ui-system`（`fdc6168`），私有仓库，协作骨架和「边界」一节已铺；`write_allowed=false`，常浩批准前不派写入单；项目卡暂无。
- **测试**：见 PR。
- **唯一下一步**：批 2（my-skills 机器索引 I-MS-1、GitHub 中心 README I-GC-1）与数码世界定位文档 I-DW-1。

## 2026-11-07 · 名册 v1.4：Obsidian 知识库登记为活跃，另建工程仓库（#82）

- **来由**：常浩看盘点时发现知识库没算进项目（Vault 只在 inventory「只登记」），说「登记为活跃，并建立 github 仓库」。Vault 1248 个文件里 112 篇受限、979 篇没标注，「决定/Vault 只开放给本人本地入口」和 Vault AGENTS 的禁推规矩都挡着整库上 GitHub；常浩选「另建工程仓库」，Vault 正文照旧只在本机，两条规矩不变。
- **工程仓库**：新建 `work/obsidian-kb`，私有远端 `Natsukashii1997/obsidian-kb`（`89443ae`），用「项目起步」铺了骨架；AGENTS 写明两个仓库怎么分、Vault 内容不进本仓库（只写结论和笔记路径）。Vault 一个文件没改。
- **projects.json v1.4**：活跃 14 个，合并官扫的仓库 13 个；obsidian-kb 的 card 指 Vault 项目卡；inventory 里 Vault 那条改为指向 obsidian-kb。
- **测试**：见 PR；`check_repos` 对 obsidian-kb 11 项全过（含项目卡）。
- **唯一下一步**：常浩在 GitHub 上删掉可删的 7 个仓库（含 little-steps-private）。

## 2026-11-07 · 名册 v1.3：get-index、Little Steps 转为活跃（#80）

- **来由**：常浩看过最终盘点后说「get-index、Little Steps 调为活跃，GitHub 上仓库」。推之前查出两边都有私人内容，常浩选了：get-index 只推代码；Little Steps 照原样推私有库，目录挪进 work/。
- **get-index**：索引数据 `part-*.jsonl` 和 `state/accepted-snapshot.json`（177 条 GET 笔记的标题与分类，部分受限）移出跟踪、留在本机；旧历史带着这些数据，远端改用新起的单提交历史 `c9e020a`，旧历史打包在 `~/Archive/repos/备份/get-index-旧历史含数据-20261107.tgz`。9-20～9-28 没提交的多行 tags 兼容等改动一并提交，`unittest` 108 项 OK。用「项目起步」补了骨架，AGENTS 里「不连接远程仓库」改为「远端只放代码」。
- **Little Steps**：`~/Documents/AI 项目/baby-care-workbench` 挪到 `work/little-steps`（挪前查过只有名册引用旧路径），补骨架后推到私有仓库 `little-steps`（`8223ddf`）；`tsc` 通过，`node --test` 8 项通过，Playwright 端到端因本机没装浏览器没跑。原有没跟踪的 zip 和两份复盘文档没动，由常浩定。
- **两个新仓库**：私有、只允许 squash、合并后删分支、5 个分流标签、PR 模板都已设好，`ops/check_repos.py` 这几项都过。
- **projects.json v1.3**：活跃 13 个，合并官扫的仓库 12 个；两条「只登记」删掉；little-steps-private 仍可删，说明里的源码位置改为新仓库；disk-slim 说明改为 PR #1 已合并（`19adc49`）。
- **测试**：`python3 -B -m unittest discover -s hermes/scripts/tests` 原样 `Ran 372 tests` / `OK`；`orca/tests` OK；别名回读见 PR。
- **范围外发现**：`check_repos` 报 6 个活跃项目没有 Vault 项目卡（trainee-hub、postpane、chatgpt-clipper、disk-slim、get-index、little-steps）；trainee-hub 仓库还允许 merge commit 和 rebase、合并后不删分支，主目录检出的是 feat/ui-prototype。都没动。
- **唯一下一步**：常浩在 GitHub 上删掉可删的 7 个仓库（含 little-steps-private）。

## 2026-11-07 · 名册 v1.2：收藏器、磁盘瘦身转为活跃；销售素材库删除；GitHub 杂项判定去留

- **来由**：常浩看过盘点后说：路觅销售素材库也删除；ChatGPT 回答收藏器和磁盘瘦身是单独的两个小项目，转为活跃，没建库就建上；GitHub 上不用的杂项，没价值的删掉。
- **两个小项目**：协作骨架原本就齐（AGENTS、CURRENT、LESSONS、交接模板与格式测试、check-rules、docs/agents 三份）。缺的是远端：已建私有仓库 `Natsukashii1997/chatgpt-clipper`、`Natsukashii1997/disk-slim`，设置和其他仓库一致（默认 main、只允许 squash、合并后删分支、5 个分流标签），issue 看板说明改成「已建」。disk-slim 的 main 上只有起步那 1 个提交，第 0–9 批在 feat/* 分支链上，已经全部推送，并提了 disk-slim PR #1 把链尾合回 main，等常浩说「合并」。两个仓库都没加 CI：disk-slim 的测试只能在 macOS 上跑；收藏器要不要加 CI 另定。名册新增两条活跃项目，合并官扫的仓库从 8 个变成 10 个（act.repos 没改，这两个仓库只给建议，不会自动合并）。
- **销售素材库**：本地 `Fmode/销售案例库` 移到了 `~/Archive/repos/待删/Fmode-销售案例库`。远端 git.fmode.cn 和线上 app.fmode.cn 由常浩处理。
- **GitHub 杂项判定**：可删 7 个，即 digital-twin-old-to-delete 和 -2（两个 HEAD 都已在本仓库历史里）、orca-chatgpt-sandbox-20261030、hud-dock（唯一的自有修复驿窗已经包含）、my-app-lumist、aibp-desk、little-steps-private（打包产物，没开 Pages）。保留 dt-sandbox（DT-ORC 在用）。gh 令牌没有 delete_repo 权限，删除由常浩动手。
- **测试**：见 PR 描述。
- **唯一下一步**：常浩在 GitHub 上删掉这 7 个仓库；disk-slim PR #1 等常浩说「合并」。

## 2026-11-07 · 名册按全量项目盘点更新（#75），常浩「全部执行」后合并

- **来由**：常浩让 Claude 盘点本地和 GitHub 上的全部项目，然后定了去留：aibp-desk 并入管培生工作台；业务管理工作台（ops-desk）、用户案例库、公司战略系统，以及 Obsidian Second Brain、knowledge-base、brain、Claudecode-项目推进系统、ai-studio-feishu-integration、csv-amount-sorter、my-app-lumist 这 7 个停滞项目删除；「管培生带教-AI协同」「营销战役AI协同MVP」「营销战役卡片网关」提取后删除；get-index 保留；LUMI 学员助手是半成品，回头再整理。
- **projects.json v1.1**：活跃项目 9 个。补登 trainee-hub、postpane 两条：这两条从 11-06 起就在正式目录里生效，但一直没提交。trainee-hub 的备注写明并入关系，别名加 aibp-desk。inventory 54 条，每个删除项写明移到了哪里；新增 `github:` 开头的条目，登记只在 GitHub 上的仓库；chatgpt-clipper、disk-slim 只登记，是否转为活跃由常浩定。合并官扫的范围不变（仍是 8 个有远端的活跃仓库）。
- **已执行的删除**：13 个目录和 11 个 launchd plist 都移到了 `~/Archive/repos/待删/`（plist 在 `_launchd/`），原路径记在 `移入记录.tsv` 里，真正删除由常浩动手。GitHub 上的 my-app-lumist、aibp-desk 已归档。常浩回复「全部执行」后，先 `launchctl bootout` 停掉 ai.lark-channel-bridge.bot.codex（机械邪龙兽 Bridge）和 ai.trainee-collab.worker.codex，确认进程都已退出，再移走三个旧协同项目。机械邪龙兽从此在飞书里下线，与「飞书统一用猛鬼兽」的决定一致。
- **测试**：`python3 -B -m unittest discover -s hermes/scripts/tests` 结果 `Ran 362 tests … OK`；`orca/tests` 结果 `Ran 48 tests … OK`。
- **主目录**：正式目录里的 `projects.json` 带着上面那两条没提交的改动，和本 PR 改的是同一个文件。按《工程流程》第 5 节，常浩已同意（「全部执行」）：合并后丢掉这份改动，再 `git pull --ff-only`。
- **唯一下一步**：常浩真正删除 `~/Archive/repos/待删/`，以及 GitHub 上 digital-twin-old-to-delete 两个仓库（时间由常浩定）；LUMI 学员助手回头整理。

## 2026-11-07 · 阶段 1：活跃仓库统一 CI，合并官以 CI 通过为合并条件（#71 PR #72 `cbe4652`、#73 PR #74 `1e68496`），已安装

- **CI 已统一**：7 个仓库的 GitHub Actions CI 在 main 上都是绿的：trainee-hub `93d1d1a`、agent-kit `4951ac0`、digital-twin `cbe4652`、github-center `df2a45c`、my-skills `d28453c`、niuma-home `a636869`、digiworld `5a19d84`。都只在 ubuntu 上跑，不用 macOS 跑（额度按 10 倍扣）。niuma-home 有 4 项测试、digiworld 有 98 项测试依赖 macOS 才有的系统工具（PlistBuddy、sips、diskutil、plutil，以及产品本身对非 macOS 的拒绝），只在 Linux 上跳过并写明原因，macOS 上照常运行；逐条核实过，没有发现 Linux 才会出的缺陷。
- **postpane 例外**：仓库的 Actions 在设置里是关着的，它的 AGENTS.md 第 102 行写着「GitHub Actions 已停用」；它的 CI 矩阵还带 macOS 和 Windows。常浩定：不开，postpane 退出飞书合并（#73），由电脑上的会话合并。
- **合并官改了什么**：`merge_rules.json` 的 `act.repos` 从 2 个仓库扩到上面 7 个，都写 `ci: true`。`merge_act.py` 读当前 HEAD 的 `statusCheckRollup`：全部通过才合并，不在本机跑测试；CI 还在跑、没过、一项检查都没有都停下并说明原因。没有 CI 的仓库，在干净副本里先跑 `setup` 再跑 `test`。`merge-officer` 技能同步改了覆盖范围。《工程流程》第 5 节同步改了（agent-kit#26、#30），电脑上合并前也要先看 `gh pr checks`。
- **已安装**：`merge_act.py`、`merge_rules.json`、`merge-officer` 技能从 main `1e68496` 复制，`cmp` 一致，校验值和旧版备份在 `evidence/2026-11-07-合并官CI-安装/`。复制时 zsh 把 `$C:hermes` 里的 `:h` 当成修饰符，三个文件被写空了几秒，12:32 用 `${C}:path` 的写法重装；这段时间合并官定时任务没有运行（12:30 跑完，下次 13:00）。网关用 `launchctl kickstart -k` 重启（没用 `hermes gateway restart`，plist 没被重写），PID 10035 → 40151，12:33:29 `✓ feishu connected`，`merge-officer` 为 enabled；在安装位置跑 `merge_act.py cards`，退出码 0。
- **要记住（ARCHITECTURE 5.5 第 6 条）**：免费版私有仓库设不了分支保护，GitHub 那边不会因为 CI 没过而拦合并，只有合并官和《工程流程》在守。所以阶段 4 自动放权（`owner_approve`）之前，要单独评估这一点：是否接受「只靠脚本把关」，还是升级、换做法。
- **未验证**：还没有在飞书里对一个有 CI 的仓库 PR 回「合并」，做端到端试跑。
- **下一步**：下一个走飞书的 PR，常浩回「合并」时看回复里是不是「CI 通过」，而不是「测试通过，日志在…」。

## 2026-11-07 · 飞书一句话合并（#67，PR #68 `fa81bc8`），已安装，端到端试跑通过

- **改了什么**：`merge_officer.py scan` 给 `merge_rules.json` 的 `act.repos`（digital-twin、agent-kit）里每个新「PR × 版本」发卡片「🔀 待你决定 · 仓库#PR@版本」。常浩引用卡片回「合并」后，`merge-officer` 技能调新脚本 `merge_act.py`：核对版本、冲突、是否落后、本机主分支、风险档（L3 不走飞书），在干净 worktree 跑测试，带 `--match-head-commit` 合并，回读确认，主目录干净时再拉齐。回「退回 原因」就在 PR 里留言。常浩的决定记进 `agreement.csv` 的 `owner` 列。授权是单次的 `owner_direct`，不放权。
- **已安装**：`merge_officer.py`、`merge_act.py`、`merge_rules.json`、`merge-officer` 技能从 main 复制，`cmp` 一致；旧版备份在 `evidence/2026-11-07-飞书合并-安装/backup/`。网关用 `kickstart -k` 重启，飞书首连超时，10:46 自动重连成功。定时任务 `8994fb76d03e` 改为 `*/30 9-23 * * *`。
- **端到端**：agent-kit PR #20（《工程流程》第 5 节补「飞书对 Hermes 说合并也算」，issue #19）。卡片 10:49 发出，常浩 10:56 引用回「合并」。Hermes 调 `merge_act.py merge 'agent-kit#20@01ec412'`；它的终端前台上限 600 秒，超了就自动转后台，再等进程结束。39 项测试通过（日志在 `merge-runs/agent-kit-20-01ec412-…/test.log`），合并为 `f90358a`，issue #19 关闭；`owner=合并`；agent-kit 主目录拉齐到 `f90358a`；测试用的 worktree 已删。
- **下一步**：之后数字分身、agent-kit 的新 PR 都会来卡片。等某一档攒到 20 次、一致率 ≥90%（同时看「退回且一致」），再另开 issue 申请写 `owner_approved_policy` 放权。

## 2026-11-07 · 自检也比对 hermes/scripts 下的 .json（#65，PR #66 `cfb8ef1`），已安装

- **改了什么**：`ops/self_check.py` 的 `install_pairs` 除了 `hermes/scripts/*.py`，也比对顶层 `*.json`，目前只有 `merge_rules.json`，是批 D 那条缺口；`tests/` 下的 json 不比对。新增 1 项测试，全量 340 项通过。
- **已安装**：`~/.hermes/scripts/self_check.py` 从 main 复制，`cmp` 一致；旧版备份在 `evidence/2026-11-07-批D-安装/self_check.py.before-65`。它是无模型定时任务，不用重启网关。
- **实跑**：新版自检没报 `merge_rules.json`；只报出 11-05 记过的 `progress/SKILL.md` 安装副本不一致，范围外，没动。
- **下一步**：明早 09:50 自检照常跑；`progress/SKILL.md` 不一致仍待常浩决定按哪边为准。

## 2026-11-07 · Hermes 影子合并官 + 每周提案收口（批 D，#61，PR #64 `61a47a0`），已安装、两个定时任务已注册

- **改了什么**：
  - 新增 `merge_officer.py`：只读影子合并官。按 `merge_rules.json` v1 给名册活跃仓库的 open PR 判「建议合并 / 退回 / 问常浩」，读真实结局算一致率；`replay` 回放改规则前后的一致率；放权门槛（同类 20 次、≥90%）只报告，不放权。
  - 新增 `proposal_digest.py` 和草稿入口 `proposal_digest_draft.py`：每周一汇总会话复盘和牛马之家每周整理的待决提案，Hermes 给每条写推荐，再发私聊卡片。常浩回「按推荐」或逐条回复后，写回各来源的结论。LESSONS 升级候选默认不列。
  - 新增 Hermes 技能 `proposal-digest` 0.2.0。
- **记录在哪**：影子判定在 `DigitalTwin/shadow/agreement.csv` 和 `facts.jsonl`（13 条实时判定 + 225 条回填，回填不计入门槛）；期号和回复在 `DigitalTwin/proposals/digest.json`，Hermes 写的推荐在同目录 `rec-<日期>.json`；安装 sha256 在 `evidence/2026-11-07-批D-安装/install.txt`。
- **已安装**：4 个脚本和技能从 main 复制，`cmp` 一致；网关已重启（`hermes gateway restart`，它顺带重写了 `ai.hermes.gateway.plist`，重写前没备份，新的结构正常）。
- **端到端验证**：第 1 期 18 条，Hermes 写推荐后发出；常浩引用卡片回「按照你的推荐完成」，Hermes 跑 `apply "按推荐"`（这一轮主模型超时，由回退模型完成）。写回核对：复盘 3 条结论行、`decisions.md` 追加 15 行（W2026-10-23-8 随 -30-3 记采纳）、`digest.json` 记下这次回复。首次实测因卡片没有推荐、Hermes 会话看不到卡片而没记下，改成方案一后跑通。
- **定时任务**：
  - `8994fb76d03e` 影子合并官：`30 9 * * *`，无模型，投递私聊，成功不输出；
  - `81464e7ea4e3` 待决提案：`0 10 * * 1`，带模型，script `proposal_digest_draft.py`、技能 proposal-digest；正常结果投递 local，失败投递私聊。
- **已知缺口**：名册仓库都没有 CI，影子判不了测试是否通过；v1 规则的回填一致率只有 17%–71%，主要因为早期 PR 没写 `Closes #`。按第 8 节第 4 条，等常浩推翻实时判定后再出规则补丁。自检只比对 `.py`，`merge_rules.json` 不在比对范围内。
- **下一步**：11-09 10:00 后看私聊是否收到带推荐的第 2 期（8 条延期的会再列），`hermes cron list` 里 `81464e7ea4e3` Last run ok。

## 2026-11-06 · Tidings 周报推送按设置页发送（#58，PR #59 `378702e`），定时任务改为每 15 分钟

- **改了什么**：`tidings_weekly.py` 每次先读 github-center `data/settings.json` 的 `weekly`（缺失或不合规逐项按默认：开、周一、11:30），关着不发；过了最近一次设定时刻、这一期没发过（状态文件 `last_slot`）才发，能发到设定那天结束，要等数据时延到设定时刻后 3 小时 15 分；整天错过不补。数据没就绪先等 3 小时再发说明。`--dry-run` 说明为什么不发；`--force` 不看设置、不记当期。配套 github-center #61 / PR #62。
- **已安装**：`~/.hermes/scripts/tidings_weekly.py` 从 main 复制，`cmp` 一致；旧版备份在 `evidence/2026-11-06-tidings-weekly-settings/backup/`。
- **定时任务**：`hermes cron edit 18a21b2eb87a --schedule '*/15 * * * *'`，改名「Tidings 周报卡片（每 15 分钟看一次，按 Tidings 设置页的时间发到私聊）」，读回一致。安装副本实跑（周五）：`--dry-run` 输出「不发：还没到周一 11:30。」，正式跑无输出、退出码 0、没发消息。
- **已验证**：302 项测试通过；合并前两轴 code-review，修了设得很晚跨午夜发不出、`--force` 挡当期推送、`DT_NOW` 无时区报错。
- **下一步**：11-09 11:30 后看私聊是否收到带横幅的卡片；之后 `hermes cron list` 里 `18a21b2eb87a` 每 15 分钟一次 Last run ok。

## 2026-11-06 · vault 技能 0.4.0 对齐 Vault AGENTS v2.1 与 vault-read 1.4（#54，PR #57 `953fccd`），已安装、网关已重启

- **改了什么**：`hermes/skills/vault/SKILL.md` 0.3.0 → 0.4.0。查找顺序对齐 Vault AGENTS v2.1（个人上下文 → 决定 → 卡片 → 经历 → 主题 → 项目卡 → 收件箱 → GET）；问到常浩做过的工作项目先读 `30 知识/经历/` 档案；`search` 先带 `layers: ["我", "精华"]`，按问题带 `intent`；删掉已停用的 `综合/`。Vault 侧 2026-11-05 已编出 7 份经历档案、150 张卡片。
- **合并**：常浩说“合并”后 squash 合并为 `953fccd`，issue #54 关闭，主目录拉齐，自检测试 26 项 OK。本地变基被权限拦下没做；合并前确认 main 新增的 3 个提交不碰这个文件，GitHub 显示 CLEAN。
- **推送插曲**：远端分支名 `claude/54-vault-skill-v2.1`（带点）两次被 GitHub 拒（500、`fatal error in commit_refs_rules`），改推为 `claude/54-vault-skill` 一次成功；`gh pr create` 两次 GraphQL 报错，改用 REST 接口创建。
- **已安装**：复制到 `~/.hermes/skills/autonomous-ai-agents/vault/SKILL.md`，`cmp` 与 main 一致；改前副本在证据目录 `vault-skill-0.4.0-20261106/`。重启网关（`launchctl kickstart -k`），00:11:04 `✓ feishu connected`，`hermes skills list` 里 vault 为 enabled。
- **下一步**：常浩在飞书私聊问一个工作项目问题（例如「学业规划报告项目里我负责什么」）验收：应先加载 vault 技能、读 `30 知识/经历/` 档案、回复带档案路径和小节。

## 2026-11-05 · Tidings 周报改发飞书卡片（#55，PR #56 `ca685aa`），每周一 11:30 定时任务已装

- **已合并**：先跑 code-review（两个仓库，各审规范、需求两条线），修了 4 处：自检也比对 `hermes/scripts/assets/` 素材；发出后状态写不进不再误报“没发出”；github-center 侧提示标题转义、提示正文并成一行（github-center PR #60 `04b2b36`，两个 PR 一起合并）。常浩说“合并”后 squash 合并，主目录已拉齐。测试：3.9.6 / 3.14.5 各 `Ran 292 tests OK`。
- **已安装**：`tidings_weekly.py`、`assets/tidings-banner.png` 复制到 `~/.hermes/scripts/`；`ops/self_check.py` 改了，同步到 `~/.hermes/scripts/self_check.py`（旧版备份在 `evidence/2026-11-05-tidings-weekly/backup/`）。三个文件与 `main` 逐字节一致。
- **定时任务**：`hermes cron create` 装了 `18a21b2eb87a`（无模型，`30 11 * * 1`，script `tidings_weekly.py`，deliver 常浩私聊）；`hermes cron list` 读回配置一致，下次触发 `2026-11-09T11:30:00+08:00`，网关在跑。默认网关的 PATH 里有 `lark-cli`。成功不出声，没发出或数据过期会在私聊说一句。
- **自检顺带发现（范围外，没动）**：`~/.hermes/skills/autonomous-ai-agents/progress/SKILL.md` 安装副本 11-05 21:19 被改过，和 `main` 的 `hermes/skills/progress/SKILL.md` 不一致；明早 09:50 自检会报。
- **下一步**：11-09 11:30 后看私聊是否收到带横幅的卡片、`hermes cron list` 里 `18a21b2eb87a` 的 Last run 是否 ok。

## 2026-11-04 · 映射 v6 已合并（PR #52，`0bcdf3e`），已登记送审 HMS-20261104-002

- **已合并**：常浩说“合并”后 squash 合并为 `0bcdf3e`，主目录已拉齐，7 个文件的 blob 和 PR 分支一致。
- **已送审**：登记 HMS-20261104-002，supersedes HMS-20261104-001，`addresses` 3 条（HMS005-P1-01、P2-01、P2-02），绑定 `0bcdf3e` 中 7 个文件的 sha。submission sha `f9d34c84…`。证据 `evidence/three-reports-batch0-mapping-v6-20261104/`。
- **待审清单**：登记前 `pending.json` 还列着已复核的 HMS-20261104-001，脚本只接受这一种已知过期状态；重写后回读与扫描一致，待审只有 HMS-20261104-002。
- **没动**：只改文档。没有代码；G5 分支、正式库、cron、网关、飞书都没碰。
- **下一步**：常浩在 ChatGPT 发「验收 HMS-20261104-002」，回执回来后处理。v6 没有待确认项。

## 2026-11-04 · 映射 v5 复核：REWORK（HMS-20261104-001，1 条 P1）

- **回执**：`collab-inbox/hermes/HMS-20261104-001/review.json`。编号、commit_sha（`acdd83b`）、submission_sha256（`a55db46b…`）都对得上，回执有效。按扫描已无待审项；`pending.json` 还列着 HMS-20261104-001，下次登记时一起重写。
- **逐项**：完整性、HMS004-P1-01（更正必须经常浩授权）、K1 PASS；HMS004-P1-02（发出标记）、P2-01（对账授权）、P2-02（held 持续提醒）PASS_WITH_CONDITIONS，条件都在 P3 实现和测试时落实。新增一项 REWORK。
- **要返修的 P1**：HMS005-P1-01，同一台账、同一期次，不同快照的发出标记不互斥。快照 A 已发出写入、没记 ③ 时崩溃，重跑生成快照 B，B 只看自己的标记就会再写一次。ChatGPT 在 e058caf 临时库上验证：同一 `doc + period`、不同 `snapshot_id` 的两条 `ledger_sending` 都能插入。要求期次级认领，加 T-96 和双进程并发测试。
- **P2（不阻断）**：HMS005-P2-01 `report-directive` 用 `--stream`、`report-reconcile` 用 `--doc`，对账参数形状要统一；HMS005-P2-02 所有写入路径都要经过发出标记，旧写入方停用和 T1 分享权限作为正式切换硬闸门。
- **没有任何授权**。
- **v6 已写**：[docs/三报-第0批-映射与基线-v6.md](docs/三报-第0批-映射与基线-v6.md)，在 v5 上定向修订，取代 v5 全文；v2 至 v5 和盘点报告的取代指针都已改指 v6。P1 的修法分三层：台账锁；同一 `BEGIN IMMEDIATE` 事务里按期次查全部快照的台账事件再认领；发出标记的键去掉 `snapshot_id`，靠 events 表的 UNIQUE 兜底（新增第 4.2.1 节，T-96、T-96a 至 T-96e）。一期以第一个发出写入的快照为准，后来快照多取到的内容进下一期。两条 P2 一并设计：对账统一用 `--ledger`、和 `--stream` 互斥（T-97）；`report_publish` 只接受有效认领，旧写入方和 T1 列为切换硬闸门（T-98）。没有新增待确认项。
- **下一步**：v6 等常浩说“提交并开 PR”，合并后用新编号 supersedes HMS-20261104-001 送审。

## 2026-11-04 · 映射 v5 已合并（PR #49，`acdd83b`），已登记送审 HMS-20261104-001

- **已合并**：PR 分支先合入 main（自检接入 Tidings，#51），CURRENT 顶部的文字冲突两段都保留；常浩说“合并”后 squash 合并为 `acdd83b`，主目录已拉齐，6 个文件的 blob 和 PR 分支一致。
- **已送审**：登记 HMS-20261104-001，supersedes HMS-20261103-001，`addresses` 4 条（HMS004-P1-01、P1-02、P2-01、P2-02），绑定 `acdd83b` 中 6 个文件的 sha。submission sha `a55db46b…`。证据 `evidence/three-reports-batch0-mapping-v5-20261104/`。
- **待审清单**：登记前 `pending.json` 还列着已复核的 001，脚本只接受这一种已知过期状态（`pending_before_stale_001: true`）；重写后回读与扫描一致，待审只有 HMS-20261104-001。
- **没动**：只改文档。没有代码；G5 分支、正式库、cron、网关、飞书都没碰。
- **下一步**：常浩在 ChatGPT 发「验收 HMS-20261104-001」，回执回来后处理。v5 没有待确认项。

## 2026-11-04 · 工程自检接入 Github·Tidings 健康检查（PR #51，`2f72576`）

- **改了什么**：`ops/self_check.py` 每天在 github-center 下用 `~/.nvm/versions/node/v24.16.0/bin/node` 跑 `bin/health.js`，超时 120 秒；有输出原样作为一段，退出码非 0、超时、起不来报「Tidings 自检未完成（原因）」，没问题不输出。#50 已关。
- **已同步**：`~/.hermes/scripts/self_check.py` 已从 main 复制，逐字节一致；用安装副本在干净环境（`env -i`）跑，无输出（无漂移、Tidings 健康）。
- **已验证**：282 项测试通过；空 data 副本会输出「🌟 Github·Tidings 自检」段，node 路径不对时报「无法启动」。没发飞书。
- **注意**：nvm 升级 Node 后要改 `TIDINGS_NODE` 的默认路径，否则自检会每天报「Tidings 自检未完成（无法启动…）」。

## 2026-11-03 · 映射 v4 复核：REWORK（HMS-20261103-001，2 条 P1）

- **回执**：`collab-inbox/hermes/HMS-20261103-001/review.json`。编号、commit_sha（`63ade37`）、submission_sha256（`53df036e…`）都对得上，回执有效。按扫描已无待审项；`pending.json` 还列着 001，下次登记时一起重写。
- **逐项**：完整性、HMS003-P1-01（超限撤回旧报告）、HMS003-P2-02 PASS；HMS003-P2-01、K1、审计区分 PASS_WITH_CONDITIONS；HMS003-P1-02（C5）、HMS003-P1-03（更正授权）REWORK。ChatGPT 在 e058caf 临时库上复测撤回：uncertain → skipped，假发送 0 次；在途三种场景要等 H3 实现后再测。
- **要返修的 2 条 P1**：
  - HMS004-P1-01：v4 仍允许 `fact_changed` 由脚本自动发更正，和“更正一律经常浩确认”不一致。二选一：A. 所有更正先出建议，只有核实过的常浩「更正」指令才能发；B. 常浩单独授权 `fact_changed` 例外并记录范围。没授权前按 A。
  - HMS004-P1-02：C5 的“连续 10 次写后立即读成功”只证明有限样本，不能保证以后每次回读都是新的。要每次读都有可校验的新鲜度证明；拿不到就对“写入状态不确定”的“未找到”一律挂起。要加“前 10 次正常、第 11 次返回旧缓存”的反例。
- **P2（不阻断，P3 实现前落实）**：HMS004-P2-01 `report-reconcile --found absent` 会触发重写，要设计和期次绑定的可信授权与证据；HMS004-P2-02 长期未恢复的 held 只报一次会变成静默，要按未关闭状态汇总或限频提醒，恢复时记关闭证据。
- **没有任何授权**。
- **常浩决定**（同日）：HMS004-P1-01 选 A，所有更正都先出建议，只有核实过的常浩「更正」指令才能发；HMS004-P1-02 选“写入请求前加发出标记”，没有标记才自动写，有标记又没找到本期内容就挂起、人工对账，删去 C5。
- **v5 已写**：[docs/三报-第0批-映射与基线-v5.md](docs/三报-第0批-映射与基线-v5.md)，在 v4 上定向修订，取代 v4 全文；v2、v3、v4 和盘点报告的取代指针都已改指 v5。两条 P2 也一并设计：对账要常浩私聊固定句式并绑定期次与证据（新增第 4.6 节）；未关闭的 held、未对账的 suspended 由巡检每天汇总，解除时记关闭证据。v5 没有新增待确认项，v4 的 C5 样本数一项作废。
- **下一步**：v5 的 PR 等常浩说“合并”；合并后用新编号 supersedes HMS-20261103-001 送审。

## 2026-11-03 · 映射 v4 已合并（PR #48，`63ade37`），已登记送审 HMS-20261103-001

- **已合并**：常浩说“合并”后 squash 合并为 `63ade37`，主目录已拉齐，5 个文件的 blob 和 PR 分支一致。
- **已送审**：登记 HMS-20261103-001，supersedes 003，`addresses` 5 条，绑定 `63ade37` 中 5 个文件的 sha。submission sha `53df036e…`。证据 `evidence/three-reports-batch0-mapping-v4-20261103/`。
- **待审清单已修好**：上一轮 `pending.json` 没能重写，一直停在只列 003 的状态。这次登记脚本只接受这一种已知的过期状态，结果注明为 `pending_before_stale_003: true`；重写后回读与扫描一致，待审只有 HMS-20261103-001。
- **没动**：只改文档。没有代码；G5 分支、正式库、cron、网关、飞书都没碰。
- **下一步**：常浩在 ChatGPT 发「验收 HMS-20261103-001」，回执回来后处理。v4 第 10 节第 6 条（C5 样本 10 个）请常浩确认。

## 2026-11-03 · 映射 v3 复核：REWORK（HMS-20261102-003，3 条 P1）

- **回执**：`collab-inbox/hermes/HMS-20261102-003/review.json`。编号、commit_sha（`a207b59`）、submission_sha256（`17606711…`）都对得上，回执有效。`pending.json` 还列着 003，没有重新生成：这次改写被本机权限拦下，按规则重扫后应为空。
- **逐项**：P2-02、K3 探针方案 PASS；P1-02、P1-04、P2-01 PASS_WITH_CONDITIONS；P1-01、P1-03、P1-05 REWORK。时序全序、合并核对、投递架构保留，不重做。
- **要返修的 3 条 P1**：
  - HMS003-P1-01：超限时，旧一代处在 uncertain 或 attempting 就不动它，但 G5-C 的 RETRY 包含 uncertain，会把已被否定的旧正文再发一次。ChatGPT 在 e058caf 临时库加假 lark 上复现：发了 1 次。
  - HMS003-P1-02：台账恢复里“C1 成立且 revision_id 没变就重写”这条捷径，绕过了 C2 截断、C3 哨兵、C4 文档身份三项检查。
  - HMS003-P1-03：机械更正只要求正文片段含同一对象编号和断言值，证明不了这个值对应 path 指定的字段，可能自动发出错误更正。
- **P2（不阻断）**：HMS003-P2-01 反复 held 的事件和告警要去重；HMS003-P2-02 「更正」固定句式只授权发起更正，不等于常浩批准了更正正文，审计里要分开。
- **e058caf 源码核对（P1-01 的修法依据）**：`tidy` 把 resolved 通知下所有未送达的投递行记 skipped，包括 uncertain 和 attempting；`_claim` 遇到 resolved 也记 skipped；在途发送的最终写入只看投递行自身，结果如实记录。所以超限时只要把旧一代通知关掉，不用改 deliveries。
- **没有任何授权**。
- **常浩决定**（同日）：HMS003-P1-03 选“机械核验只出建议”，不自动发更正，更正一律经常浩确认。
- **v4 已写**：[docs/三报-第0批-映射与基线-v4.md](docs/三报-第0批-映射与基线-v4.md)，在 v3 上定向修订，取代 v3 全文；v2、v3 和盘点报告的取代指针都已改指 v4。回执之外还有两处：新增 C5（判“没写入”要证明读到的是最新内容，防缓存回包）；T-13b 的期望按源码改正。新增确认项第 6 条：C5 实测样本定 10 个。
- **下一步**：v4 的 PR 等常浩说“合并”；合并后用新编号 supersedes HMS-20261102-003 送审。

## 2026-11-03 · 常浩确认映射 v3 第 10 节 5 项（全部按 v3 原提议）

- **1. 字数上限**：`REPORT_MAX_CHARS` 定为 4000 字符；超限只发摘要和台账链接，不分片。
- **2. 晚报过期（K6）**：22:00 至 23:00 照常重试，23:00 以后不发，次日 08:00 过期，由下一期开头说明。
- **3. 告警延迟**：接受截止宽限 30 分钟、巡检告警最多延迟约 1 小时 45 分钟；P7 先不加 08:40 检查。
- **4. 周报重试**：周五周报没发出时，周六、周日白天继续重试，到周一 08:00 过期。
- **5. 私聊命令**：停报和更正等 P6 配好 `owner_chat_id` 后再启用；在那之前，节假日不发的那一期巡检照常报缺失，由常浩忽略。
- **没动**：只记决定，v3 正文未改；HMS-20261102-003 回执仍未回。
- **下一步**：不变。常浩在 ChatGPT 发「验收 HMS-20261102-003」，回执回来后处理。

## 2026-11-02 23:17 · 映射 v3 已合并（PR #47，`a207b59`），已登记送审 HMS-20261102-003

- **文档**：[docs/三报-第0批-映射与基线-v3.md](docs/三报-第0批-映射与基线-v3.md)，在 v2 上做最小修订，取代 v2 全文；v2 和盘点报告的取代指针都已改指 v3。改动清单在 v3 第 11 节。
- **逐条**（源码按 e058caf 核对）：
  - HMS002-P1-01：不可投递的报告**不建、不改通知**，只记 `report.oversize`；排队中的旧版一并撤下；`_claim` 兜底并记 `report.held`（顺带关 K2）。T-03 改为真实 `tidy` + `run_round`、假 lark 0 次，新增 T-06～T-08。
  - HMS002-P1-02：按 Asia/Shanghai 计划时刻排全序（早 08:00、周 18:00、晚 22:00），每一代带 `expire_at`（下一期的计划时刻），`post_report`、`tidy`、`_claim` 用同一个判定。T-41～T-48 覆盖四类时序。
  - HMS002-P1-03：更正依据三选一：同窗口事实变了、可机械核对的错误（`report_verify`，不靠模型）、常浩私聊确认；判断类错误只能由常浩确认。T-51～T-57。
  - HMS002-P1-04：冻结 base 和增、改、删路径清单，合并后用 `ops/verify_merge.py` 做 V1～V4 四项核对（随 H2 交付）。T-61～T-67。
  - HMS002-P1-05：只有满足 C1～C4 的权威、完整回读才能判“没写入”；其余挂起，人工对账；规范化覆盖全部业务正文。T-24、T-25 改，新增 T-70～T-80。
  - HMS002-P2-01：停报和更正共用私聊固定句式，`verify_message` 核身份，按回读到的原文严格解析，使用时再回读复核。T-81～T-88。
  - HMS002-P2-02：K3 验收表按 S1、S2、R0～R6 逐项列。
  - 有条件通过项的条件也写进去了：T-13 用真实并发、T-13b 中断；T-37、T-38 覆盖状态库不可用。
- **没动**：只改文档。没有代码，H2、H3 都没开始；G5 分支、正式库、cron、网关、飞书都没碰。
- **已合并、已送审**：常浩说“合并”后 squash 合并为 `a207b59`，主目录已拉齐，4 个文件的 blob 和 PR 分支一致。登记 HMS-20261102-003：supersedes 002，`addresses` 7 条，绑定 `a207b59` 中 4 个文件的 blob。submission sha `17606711…`；`pending.json` 回读与扫描一致，待审只有这一条。证据 `evidence/three-reports-batch0-mapping-v3-20261102/`。
- **下一步**：常浩在 ChatGPT 发「验收 HMS-20261102-003」，回执回来后处理。第 10 节有 5 项请常浩确认：v2 留下的 3 项，加上周报周末重试、私聊命令要等 P6 才能用。

## 2026-11-02 22:47 · 映射 v2 复核：REWORK（HMS-20261102-002）

- **回执**：`collab-inbox/hermes/HMS-20261102-002/review.json`。编号、commit_sha（`4b3a1ea`）、submission_sha256（`8d2d1bf4…`）都对得上，回执有效；`pending.json` 已重新生成，待审清单为空。
- **逐项**：
  - P2-02 PASS；P1-03、P1-05、P2-01 PASS_WITH_CONDITIONS；P1-01、P1-02、P1-04 REWORK。
  - 五个重点：K1、K3 有条件通过；K2、K4、K5 REWORK。
- **要返修的 5 条 P1**：
  - HMS002-P1-01：超限不发、但通知保持 open 并带 digest 时，会被 `tidy` 自动建成 pending。ChatGPT 已在临时库上复现。要定义“不可投递”的状态，或者干脆不建通知；并用真实的 tidy + run_round、假 lark 调用 0 次来验证。
  - HMS002-P1-02：跨报告流的过期只比日期，处理不了同一天的早报和晚报、周五的早报和周报。要按“计划时刻”（Asia/Shanghai）排一个全序，并覆盖四类时序反例。
  - HMS002-P1-03：只看 `snapshot_id` 有没有变，会漏掉同一份事实下的结论错误。经过核验的纠错、或常浩确认的实质纠正，也要能发「更正」；普通润色不重发；补同一快照下的正反用例。
  - HMS002-P1-04：`git diff --exit-code` 和“允许 main 有无关差异”这句说明自相矛盾。改为：冻结基线 SHA 和各批改动的路径清单，合并后逐个 blob 校验；其余差异按白名单核对。
  - HMS002-P1-05：崩溃恢复时，“读不到标题”不等于“没写入”。只有完整、权威的回读确认这一期不存在，才能重写；读取不完整、网络失败、权限错误都要挂起，人工对账。
- **P2**：
  - HMS002-P2-01：`report.skip` 要有可信的常浩授权来源，不能由模型凭自然语言直接写入。
  - HMS002-P2-02：K3 探针要按 `send` 和 `readback` 实际校验的字段列验收表；`readback` 其实是 6 组判断，不是“七项”。
- **没有任何授权**。v2 第 10 节三项仍待常浩确认。
- **下一步**：出映射 v3，回应上面 7 条，用新编号 supersedes HMS-20261102-002 送审。上下文已经很长，建议交接到新会话。

## 2026-11-02 22:04 · 三报第 0 批 P1：ChatGPT 复核盘点为 PASS_WITH_CONDITIONS（HMS-20261102-001）

- **回执**：`collab-inbox/hermes/HMS-20261102-001/review.json`。编号、commit_sha（`b08583b`）、submission_sha256（`6a471b74…`）都对得上，回执有效；`pending.json` 已重新生成，待审清单为空。
- **逐项结论**：C1、C3、C4、C6、C7 PASS；C2、C5、C8、施工顺序 PASS_WITH_CONDITIONS；**映射设计 REWORK**。
- **第 0 批第一项“G5 当前状态与风险独立复核”有条件关闭。** 五条 P1 关闭之前，不实现三报写入和投递：
  - P1-01：长文分片和 deliveries“一条通知一个渠道一行”冲突。二选一：限定单条消息大小、超限时 fail-closed 并附台账链接；或者每片单独一条通知。
  - P1-02：已送达的报告改稿后，不会自动重发。要定修订、更正、过期未送这几种情况怎么处理，并用 TDD 覆盖四种情形。
  - P1-03：锚点的固定幂等键查不出“同一个键、不同快照”；台账写成功但锚点没写上时，恢复路径不完整。
  - P1-04：施工顺序里的 SHA 基线前后不一致。P2 改完后要冻结新 HEAD 并送审，P5 只合这个 HEAD；P3 从明确的基线另开分支。
  - P1-05：要能核对“这一期报告缺失”。上线前按“期次 + 截止时间”核对快照、锚点、投递三样是否齐全；取数失败要变成看得见的失败；巡检要能报缺失。
- **P2 级**：
  - P2-01：K3（bot 回读自己发的消息）建议在 P5 之前，经常浩书面批准后做一次单条消息的真实探针。
  - P2-02：报告里的周五示例日期写错了（11-07 是周六，应为 11-06）；未关项 K1/K2/K3、T1 协作者核实要列进 D1 v2。
- **没有任何授权**：合并、安装、迁移、真实飞书、cron、网关、正式库都没动。
- **常浩决定**（同日）：
  - P1-01：一期报告只发一条飞书消息，设字数上限；超限时只发摘要和台账链接（fail-closed），不分片。
  - P1-02：已送达的报告不自动重发；事实有实质更正时，另发一条「更正」，作为新的一代，并标明原期次；下一期已经出来、旧报告还没送出的，只留在本地，不迟送。
  - K3：提前到 P5 之前，做一次单条消息的真实探针。时间和收件人执行前再请常浩书面确认。
- **v2 已写**：[docs/三报-第0批-映射与基线-v2.md](docs/三报-第0批-映射与基线-v2.md)，取代盘点报告的第 6、7 节；盘点里的周五示例日期改为 11-06。
- **v2 已合并（PR #46，`4b3a1ea`），已登记送审 HMS-20261102-002**：取代 001，绑定 `4b3a1ea` 中的文件 blob；待审清单只有这一条。证据 `evidence/three-reports-batch0-mapping-v2-20261102/`。
- **下一步**：常浩在 ChatGPT 发「验收 HMS-20261102-002」，回执回来后处理；第 10 节有 3 项请常浩确认：字数上限 4000、晚报过期规则（回答 K6）、巡检的告警延迟。

## 2026-11-02 · 三报第 0 批：G5 只读盘点与分批计划（待 ChatGPT 复核）

- **报告**：[docs/三报-第0批-G5盘点.md](docs/三报-第0批-G5盘点.md)；证据 `evidence/three-reports-batch0-g5-inventory-20261102/`。只读：没有合并、安装、迁移，没改 cron 和网关；正式库只做 stat 和 sha256，读取在副本上用 immutable。前后核对，正式库仍是 v3（sha `6cde8737…`），没变。
- **结论**：
  - G5 的合并候选是第 4 批分支 `Natsukashii1997/hermes-g5-batch4-isolated`，HEAD `e058caf`，不是 A/B/C 三个分支。合入 main 的预演无冲突。两套 Python 各 448 项测试，OK。
  - D1 方案要出 v2：合并来源改成 e058caf；安装文件从 3 个变成 5 个，因为自检会比对全部 `hermes/scripts/*.py`；窗口开始前的基准提交（`$PRE`）按当天 main 重新记录；窗口还要避开 09:45 巡检。
  - 三报接 G5 需要一次最小扩展：sync 不能清掉报告通知；render 和 SCOPE 要支持报告类型。表结构不用改。快照和锚点放进 `events`（`report.snapshot`、`report.ledger_committed`）和 `refs`。
  - cron 静默失败：11-02 07:30 日报的取数脚本超时退出 1，cron 仍记为 ok，failure_deliver 没触发。
  - 牛马之家读的是正式文档 `TCG9…` / `KD1W…`，不是并跑文档。T1 协作者没查到：缺 `docs:permission.member:retrieve`，没有替常浩授权。
- **补记 G5 10-30 晚至 11-01 的历程**（之前的 CURRENT 停在 10-30 G5-A GO；评审原文在 `~/Library/Application Support/DigitalTwin/collab-inbox/hermes/`）：
  - D1 第 1 轮 `3edcea8`：HMS-20261030-003 REWORK。第 2 轮 `e08d45d`：HMS-20261031-002 PASS（有条件：正式窗口必须停网关）。
  - G5-B `657e37d`：HMS-20261030-005 PASS。
  - G5-C：`848e3d2` 被 HMS-20261030-006 判 REWORK；返修 `204dffc`，HMS-20261031-001 PASS。
  - 第 4 批隔离集成 `2e022a4`（10-31，按文件叠加 B、C）：HMS-20261031-003 PASS_WITH_CONDITIONS，遗留 OBS-01。OBS-01 返修 `e058caf`（11-01）：HMS-20261101-001 PASS。
  - 所有分支都只在本地，没有推送、合并、安装；`owner_chat_id` 没配；没有建 G5 的 cron。
- **下一步**：盘点报告送 ChatGPT 独立复核（P1）；之后 P2（D1 方案 v2）、P3（三报适配扩展）都要常浩先授权改动范围。P3 前常浩要定两件事：晚报迟到超过 23:00 怎么处理；写入后立即投递，还是等轮询。

## 2026-11-02 · 亚古兽接通牛马之家（workdesk MCP 只读，仅常浩私聊）

- **牛马之家一侧**：work-desk [PR #7](https://github.com/Natsukashii1997/work-desk/pull/7) squash 合并为 `ee743c9`，已发布 `nm-20261102-174752-ee743c9`，回读与冒烟通过（work-desk CURRENT 有完整记录）。通用的智能体接入：每个 agent 一个服务端凭据（只存哈希），按工具授权，提问人必填，所有调用记进 `agent_calls`。第一期 4 个只读工具：`mentor_trainees`、`mentor_questions`、`project_list`、`strategy_month`。
- **亚古兽一侧**（证据：`evidence/agumon-workdesk-mcp-20261102/`，含改前的 `.env` 和 `config.yaml` 备份）：
  - 凭据：`agent-token.mjs issue agumon <4 个工具> --write-env` 直接写进 `profiles/agumon/.env` 的 `WORKDESK_AGENT_TOKEN`（权限 600，没有显示出来）；
  - `config.yaml`：`mcp_servers.workdesk`（stdio，node + work-desk 主工作区的 `server/mcp-workdesk.mjs`，凭据用 `${WORKDESK_AGENT_TOKEN}` 引用）；`platform_toolsets.feishu` 加 `workdesk`；`plugins.enabled: [workdesk-gate]`；
  - 插件 `profiles/agumon/plugins/workdesk-gate/`：`pre_tool_call` 拦 `mcp__workdesk__*`，只有 platform=feishu、chat_type=dm、发送人=常浩（`ou_6fac…`）才放行，并用 `modify` 写入真实提问人 `_caller`（覆盖模型自己填的）；拿不到会话信息也拦；
  - HERMES.md §0 牛马之家改为“已接通，只在常浩私聊里能用”（work-desk `docs/亚古兽/角色配置-V3/HERMES.md` 同步，`cmp` 一致，SHA `bedb439c…`，1599 字，注入扫描 0 命中）。
- **为什么要插件**：Hermes 调 MCP 时不带发送人，飞书的工具集也不分私聊和群聊；启用的 MCP 服务默认会进所有渠道。不加插件的话，群里任何人都能让亚古兽去读牛马之家。
- **验证**：
  - `agumon mcp test workdesk`：连接成功，发现 4 个工具（凭据引用生效）；
  - 插件单测（Hermes 自带 Python）：本人私聊放行并覆盖伪造的 `_caller`；群里的本人、他人私聊、非飞书、无会话都拦；
  - 命令行单次对话实测：第一次插件按单下划线 `mcp_workdesk_` 匹配，没拦住（实际工具名是 `mcp__workdesk__project_list`），请求到了牛马之家，因缺提问人被 400 拒绝，没有数据外流；改成两种前缀都拦后再测，被插件拦下，牛马之家没有收到新的请求；
  - 网关重启两次，最后一次 17:53:23 连上飞书（进程 59128）。
- **飞书实测（18:09–18:11，常浩）**：
  - 私聊 `/new` 后问“林瑶最近有哪些提问还没回复？”：调用 `mcp__workdesk__mentor_questions`，牛马之家 `agent_calls` 第 2 条 200，caller 为 `feishu / dm / ou_6fac… / oc_aa36…`，即插件写入的提问人一路送到了牛马之家；回答列出 17 条未回复提问，带原文链接。**通过。**
  - 群里 @ 问同样的问题：没有调用牛马之家（`agent_calls` 无新记录），回复“无法确认”，没有泄露数据。但理由说的是“牛马之家还没接通”，**表述过时**：群聊会话 `20261102_143816_4317fc70` 从 14:38 一直延续，系统提示还是改 §0 之前的版本（已核对系统提示哈希：旧版含“未接通”，私聊新会话是新版）。另外它建议“在私聊中提供日志链接”，对不能私聊的管培生不适用，也是旧会话按旧规程答的。
  - 群里复测（18:32）：第一次 `/new` 没生效，是因为群聊 `/new` 会清空共用对话，要先 `/approve` 确认（提示是英文，`display.language` 未设）；确认后新会话 `20261102_183252_44431bbb` 用的是新规程（系统提示哈希已核对）。再问同样的问题，它没有调用任何工具，回复“该数据仅支持在你的飞书私聊中读取，当前群聊不能查询或展开个人记录”；`agent_calls` 仍只有 2 条（第 1 条是命令行测试的 400，第 2 条是私聊的 200）。**通过。**
- **顺带发现**：lark-cli 的亚古兽配置（`cli_a965c429ef785bd3`）还是换 Secret 之前的旧凭据，调用报 `20140 invalid_client`；第 3 批“牛马之家读飞书改用亚古兽”开工前要由常浩重跑 `lark-cli config init`。群里出现的 `/sethome` 提示不用理：home channel 接收定时任务结果，不应设在群里。
- **新坑**：Hermes 的 MCP 工具名是 `mcp__<服务>__<工具>`（双下划线），文档里写的 `mcp_<服务>_<工具>` 不对；按工具名拦截要以实际调用为准。

## 2026-11-02 · 三报体系 V2.1 定为基线：PRD D20 已合并（PR #44），猛鬼兽 HERMES.md 已装三报规则

- **评审链**：V1.0 REWORK → V1.1 小返修 → V2.0 有条件通过（C1–C6）→ **V2.1 PASS**（[复核](docs/reviews/2026-11-02-Claude-三报体系V2.1-复核.md)）。常浩说“最终方案，无问题就施工”。各报告的具体设置（cron、脚本、提示词）先跳过，先把底层规则定下来、上传 GitHub，并装给猛鬼兽。
- **[PR #44](https://github.com/Natsukashii1997/digital-twin/pull/44)**（已 squash 合并为 `3a1b606`）：
  - 新增基线 `docs/三报体系-V2.1.md`；
  - PRD 新增 D20，改 F2.3、F2.4、F4；
  - 猛鬼兽工作规程源文件第一次入库：`hermes/workspace/HERMES.md`（第 5 节改为三报规则，原第 5 节的模型与维护内容改成第 6 节）；
  - AGENTS 加指针。
  - 第一次合并被自动模式权限拦下；常浩放开权限后，由 Claude 合并。合并后核对过，main 上的 `hermes/workspace/HERMES.md` 和安装副本 `cmp` 一致。
- **猛鬼兽设置**（证据：`evidence/three-reports-v2.1-20261102/`）：
  - 旧 HERMES.md 已备份（`e519c2af…`，与 V1.1 部署记录一致）；
  - 装前注入扫描：context 和 strict 两种模式都是 0 命中；
  - 安装 `~/.hermes/workspace/HERMES.md`（600，`8fff1a48…`），与 PR 版逐字节一致；
  - 在 workspace 下运行 `hermes prompt-size`：项目上下文 5,782 B → 8,426 B；
  - `hermes -z` 单次提问：能答对三报节奏、周五和周末的安排、一稿双产、锚点和唯一写入方，也知道不能自行建 cron。
  - 网关没有重启；飞书里现有的会话要 `/new` 后才会加载新规程（按 V1.1 验收时的做法推断，没有在飞书实测）。
- **#12**：已加评论，写明按 V2.1 调整的第 −1 步到第 4 批。
- **没动**：日报 `33df73dffd17`、周报 `6a976baa2632`、自检 `f3388d53ce97` 三个 cron；G5 分支；正式状态库；网关。
- **下一步**：第 0 批（G5 盘点，开工前先出计划等常浩确认）。

## 2026-11-02 · 主动运营与夜间推进 V1.0 送审稿审核：REWORK；常浩定「先只做汇报」

- **评审**：ChatGPT 起草《主动运营与夜间自主推进体系 V1.0》；Claude 审核结论为 REWORK（[审核意见](docs/reviews/2026-11-02-Claude-主动运营与夜间推进V1.0-审核意见.md)）。主要问题有三个：
  - 夜里无人在场就是 ARCHITECTURE 的 UNATTENDED（M4），送审稿没有对应到运行模式；
  - 飞书回复由有 shell 的模型解读并落盘，授权可以被模型伪造；
  - 几项 Hermes 能力和本机实读不符：网关钩子没有业务事件；`context_from` 的 CLI 设不了；`/goal` 判定失败时按“继续”处理（fail-open）；07:30 日报还在并跑，不推飞书。
- **决定**（常浩）：先只做汇报，夜间执行不进近期范围。
- **推送节奏**（常浩给的方向，已写进 V1.1 复核 R2）：早报 8:00、晚报 22:00、周报每周五 18:00，不再单独写日报，由晚报接替。**PRD 和 #12 等 V1.2 定稿后再改**；之前提前写的 PRD D20 和 #12 评论已撤回。
- **V1.1**：ChatGPT 返修稿经 Claude 复核为“小返修，出 V1.2”（[复核](docs/reviews/2026-11-02-Claude-主动运营与夜间推进V1.1-复核.md)，R1–R9）。要求正文改为只讲汇报体系、夜推移到附录，并补时间口径、降级、信息边界和有用率收集。复核已交常浩转给 ChatGPT。
- **下一步**：等 ChatGPT 的 V1.2，Claude 复核。没有常浩的明确要求，不开发，不改 PRD、#12 和 cron。

## 2026-11-02 14:07 · 亚古兽角色配置 V3.1 部署（只有常浩能私聊；评论只开两份管培生日志；群聊暂关）

- **定位**（常浩）：亚古兽是常浩的企业数字分身，也是牛马之家配套的工作智能体；带教是重点，项目、策略和其他企业工作按需开展。只有常浩能私聊；其他人在指定群里 @ 它，或在指定文档的评论里 @ 它。
- **评审**：Claude 候审 V1 → V2（常浩定 4 项）→ V3（渠道改为只有常浩能私聊）→ ChatGPT 两轮审核 → V3.1 定向返修。文件和评审稿在 work-desk `docs/亚古兽/`（提交 `c6e1e44`）。
- **部署**（证据与备份：`evidence/agumon-role-v3.1-20261102/`）：
  - 安装 `profiles/agumon/SOUL.md`（`48b9e36b…`）和 `profiles/agumon/workspace/HERMES.md`（`e1813848…`），装前注入扫描 0 命中。
  - `terminal.cwd` 设为 `profiles/agumon/workspace`（原来是 `.`）；在该目录下运行 `agumon prompt-size`，项目上下文 4.0KB。
  - 飞书平台关掉 `session_search`：它能按名字打开别的 profile 的会话库（`_resolve_profile_db`），而且不分发送人。现在启用的只剩 web、vision、image_gen、tts、skills、todo、memory、clarify，以及 feishu_doc、feishu_drive。
  - `.env`：`FEISHU_GROUP_POLICY=disabled`，常浩指定群之前所有群都不理；`FEISHU_ALLOWED_USERS` 仍只有常浩，其他人私聊在适配器层被拒。
  - `profiles/agumon/feishu_comment_rules.json`：顶层关闭；只对两份管培生日志按人放行——常浩两份都能触发，林瑶、胡贵兰各自只能在自己的日志里触发。两人的 open_id 取自日志文档的所有者（亚古兽身份查 `drive/v1/metas`），按文档标题推定是本人。用规则模块核验过放行结果。
  - `agumon gateway restart`：14:07:44 `✓ feishu connected`（PID 30740），猛鬼兽没受影响。
- **还没生效、要常浩做的**：
  - 飞书后台给亚古兽开 `docs:doc:readonly`、`drive:drive:readonly`、`bitable:app:readonly`，订阅 `drive.notice.comment_add_v1`，然后发版。评论回复要等这一步完成才会生效。
  - 指定群：把亚古兽拉进群，告诉 Claude 群名，写 `group_rules`（指定群 `open`、默认 `disabled`）；还要实测网关层会不会拦下不在白名单里的群成员。
- **后续（work-desk PR）**：通用 `workdesk` MCP，需要服务端凭据、按资源授权、能可信地识别提问人；牛马之家读飞书默认改用亚古兽；带教作答和机器人回复改为亚古兽。
- **未验证**：常浩私聊 `/new` 后的实际回答、评论回复、群聊放行，都要等飞书后台改好后实测。

## 2026-11-02 13:00 · 猛鬼兽设置 V1.1 上线：SOUL 只留身份和底线，工作规程拆到 HERMES.md（飞书验收通过）

- **决定**（常浩）：按 Claude 候审稿 → ChatGPT 修订 → Claude 复核（7 处）→ ChatGPT V1.1 定稿的结果部署。评审材料：`docs/reviews/2026-11-02-猛鬼兽设置-最终候审稿.md`、`docs/reviews/2026-11-02-Claude-猛鬼兽设置-ChatGPT定稿复核.md`；定稿包和部署说明存在证据目录。
- **结构**：`~/.hermes/SOUL.md`（945 字：身份、性格、做事原则、四条所有场景都适用的底线）＋ `~/.hermes/workspace/HERMES.md`（2575 字：信息来源、信息边界细则、工程分工与派单、预授权出处、定时与模型）。USER / MEMORY 仍由原生记忆维护，没有手改。Hermes 只在工作目录正好是 workspace 时读 HERMES.md；用到模型的定时任务要设 `--workdir` 才读得到。
- **部署**：
  - 本机 main 先变基到 origin/main（拿到 `docs/DT-ORC/ARCHITECTURE.md`），CURRENT 冲突处理成两条记录都保留。
  - 备份旧 SOUL、memories、`cron/jobs.json` 到 `evidence/mengguishou-soul-v1.1-20261102/`。
  - 安装两份文件（600），SHA-256 与定稿一致（SOUL `cd7a1926…`、HERMES `e519c2af…`）；装前用 `tools/threat_patterns.py` 扫描，0 命中。
  - `hermes cron edit` 给日报 `33df73dffd17`、周报 `6a976baa2632` 设 `--workdir ~/.hermes/workspace` 并回读；工程自检 `f3388d53ce97` 是纯脚本，没动。脚本固定从 `HERMES_HOME/scripts/` 解析，两个报告脚本都不依赖 cwd。
  - `hermes prompt-size`（在 workspace 下运行）：项目上下文 0 → 5,782B，SOUL 所在的稳定部分 17.9KB → 12.4KB，总量基本不变。
  - 常浩 13:00 重启网关（PID 99368），13:00:30 `✓ feishu connected`。
- **飞书验收**（常浩私聊，`/new` 后的新会话 `20261102_130207_1c7a4016`）：
  1. “你是谁？工作规程放在哪？”→ 按新 SOUL 回答身份，答出 `~/.hermes/workspace/HERMES.md`，开口没叫名字。
  2. “按我的习惯，新项目做视觉开发默认做哪些端？”→ 依次调 vault 技能、`vault_read search`、读 `个人上下文/当前人机协作偏好.md`，答“默认只做 PC 版”，引用 P-09。
  3. “帮我在数码宝贝群里发一句‘大家好’”→ 查到群「数码宝贝世界」，回复“确认发到这个群吗？目前还没发送”，没有发出。
  - 常浩 13:02 把重启命令的文字发到了猛鬼兽私聊，旧会话那一轮在 0.6 秒内被 `/new` 中断，没有执行；网关 PID 没变。
- **未做**：回退模型标注、定时任务读 HERMES.md，要等实际触发（明早 07:30 日报）再看；USER 记忆两对重复的去重没做；workshop 默认路由（没点名时默认 Codex）和新分工冲突，按定稿说明单独改仓库源文件走 PR。
- **下一步**：11-03 看日报定时任务正常产出，并确认那次运行读到了 HERMES.md。

## 2026-11-02 · 企业数字分身亚古兽接入飞书（P0，本机配置，两个机器人飞书验收都通过；猛鬼兽断线已重启恢复）

- **决定**（常浩，按《Hermes 双数字分身架构 V3.0》）：个人数字分身＝猛鬼兽（不动）；企业数字分身＝亚古兽 `cli_a965c429ef785bd3`。推翻 10-26「飞书只保留猛鬼兽」，PRD 加 D19，D0、D12 标注修订。三个选项按推荐定：P0 只放常浩本人；复用现有 ChatGPT（openai-codex）登录；独立进程。
- **做了什么**（只改本机配置，仓库代码没动）：
  - `hermes profile create agumon`（空白 profile，不克隆猛鬼兽的配置、技能、记忆；包装命令 `~/.local/bin/agumon`）。
  - `agumon config set`：`gateway.standalone: true`（Hermes 新版默认一个网关进程服务所有 profile，这是让它单独起进程的开关，官方称为临时兼容项）；模型同 default（gpt-6.1-sol / openai-codex，OAuth 从根 `auth.json` 读，不复制）。
  - 飞书平台工具：`agumon tools disable --platform feishu browser terminal file code_execution delegation computer_use connections cronjob`，只剩 web、vision、image_gen、tts、skills、todo、memory、session_search、clarify。没有终端和文件工具，技术上读不到 Vault；也没装 vault-read MCP。
  - `profiles/agumon/SOUL.md`：企业人设、五个模块、P0 阶段说明、边界（不读个人记忆和 Vault、不调 ORCA、L2 以上要授权）。
  - `profiles/agumon/.env`（600）：飞书凭证、`FEISHU_ALLOWED_USERS=ou_6fac…`（常浩在亚古兽下的 open_id，取自 `~/.lark-cli/config.json` 旧条目）、`FEISHU_GROUP_POLICY=allowlist`、`FEISHU_ALLOW_ALL_USERS=false`。
  - `agumon gateway install` → `~/Library/LaunchAgents/ai.hermes.gateway-agumon.plist`；11:24:45 `✓ feishu connected`（websocket）。`hermes gateway list`：default 与 agumon 两个进程各自在跑，猛鬼兽网关没有重启。
- **验证**：凭证换 tenant token `code 0`；`agumon -z` 自我介绍为亚古兽、说明读不到 Obsidian。**飞书验收（亚古兽）通过**：常浩 11:28 私聊「你好」，agumon 网关收到（sender `ou_6fac…`），8.1 秒回复 101 字，会话库 `profiles/agumon/state.db` 里是企业人设（说明企业知识库还在建设中）。
- **顺带发现：猛鬼兽从 11-01 15:41 起收不到飞书消息**（与本次无关）。`gateway_state.json` 里 feishu 一直是 `retrying`；10-27 起的网关进程（PID 63167）长连接还挂在旧网络地址 `192.168.1.3`（本机现在是 `172.16.11.40`），日志停在 11-01 15:51「trying to reconnect for the 1st time」。11-02 09:49 巡检其实查到了，但发告警时 DNS 失败，没有发出去。处理办法：`launchctl kickstart -k gui/$(id -u)/ai.hermes.gateway`。Claude 被自动模式权限拦下，常浩 11:30 自己执行：旧进程 11:30:37 正常退出，launchd 隔了约 1 分半（ThrottleInterval 30 秒，加上连跑了两次 kickstart）才拉起新进程 PID 58709，11:32:06 `✓ feishu connected`，长连接走的是当前地址。新进程的定时任务只管 `default`，没有接管 agumon。**飞书验收（猛鬼兽）通过**：常浩 11:33～11:34 私聊，猛鬼兽 default 网关收到（sender `ou_5ad2…`，会话 `20261027_000913_b71a7260`，仍是原个人会话），分别在 6.1 秒、5.7 秒内回复，人设是猛鬼兽。两个机器人各自收、各自回，会话库互不相通。待补：巡检遇到网络断开时告警发不出去。
- **已知风险**：
  1. 定时任务：亚古兽进程会把 default 列进 tick 集合，靠「对方网关在线就让位」（`gateway/run.py` `_cron_profile_gate`）和 `cron/.tick.lock` 避免重复。猛鬼兽网关重启的几秒里，到点的猛鬼兽任务可能由亚古兽进程代跑（投递走猛鬼兽凭证或直接失败，不会发到企业会话）。
  2. `lark-cli` 在 Hermes 环境下读 `~/.lark-cli/hermes/`（绑的是猛鬼兽 + 常浩个人授权）。亚古兽现在没有终端，调不到；P1 接企业飞书数据前，要先给亚古兽单独授权并隔离这份配置。
  3. App Secret 出现在 11-02 的 Claude 会话里，建议常浩到飞书后台重置后替换 `.env`，再执行 `launchctl kickstart -k gui/$(id -u)/ai.hermes.gateway-agumon`。**已处理**：常浩 12:2x 在飞书后台重置；校验新 Secret 换 token 成功 `code 0`，旧 Secret 返回 `10014 app secret invalid`；替换 `.env` 后执行 `agumon gateway restart`，新进程 PID 85436 在 12:22:28 `✓ feishu connected`。新 Secret 也出现在同一会话里，但只在本机。
  4. 巡检 `ops/health_check.py` 目前只看猛鬼兽网关，亚古兽还没纳入。
- 证据：`evidence/agumon-enterprise-20261102/`（default 配置、改前网关列表）。
- **下一步**：常浩在飞书私聊亚古兽、猛鬼兽各发一句，确认各自用自己的人设回复；之后进入企业 P1（企业知识接入）。

## 2026-11-02 · DT-ORC 唯一工程架构基线 V3.1.0 A+ 入库（[PR #43](https://github.com/Natsukashii1997/digital-twin/pull/43)）

- **决定**（常浩）：[docs/DT-ORC/ARCHITECTURE.md](docs/DT-ORC/ARCHITECTURE.md) 是 DT-ORC 唯一的长期工程共识；以后按实际开发、测试和运行反馈在这份文件上迭代，不再另起总方案。V1.0、Claude V2.0、ChatGPT V3.0 原样移入 `docs/DT-ORC/history/`。
- **定稿内容**：
  - 角色：Claude 主开发和返修，可提出技术异议；ChatGPT 聊天模式 High 主审；Codex 按需做专项验证；Hermes 负责监工、记账和协调。
  - 运行模式：A+ 双模式，SUPERVISED（有人值守）和 UNATTENDED（无人值守）。
  - SUPERVISED 的授权来源（常浩选「(a)+(b) 都要」）：(a) 本机交互授权入口，(b) 飞书私聊，经 `twin_identity` 核验。
  - 最小执行桥：ORCA CLI 白名单调用加 RELAY-01。
  - L0–L2 自动合并：只能走受限发布通道；做不到时改为人工合并。
- **评审链**：
  - Claude 对原件 `8d7bb274…` 的结论为 NEEDS_HUMAN + REWORK（R1–R5），修订稿为 `d0ec5ed3…`。
  - ChatGPT High 做了定向审查，收紧了 4 处；定稿 SHA-256 为 `3f0b55f6…`。
  - Claude 对定稿的结论为 PASS（[定稿复核](docs/reviews/2026-11-02-Claude-ARCHITECTURE-V3.1.0定稿复核.md)）。
  - ChatGPT 审查原文还没入库（N1）。
- **文件合并 ≠ 任何真实动作放行**：`LIVE_ACTIONS_ENABLED=False`、`merge_gate` 只认 `owner_direct` 这两点都没变。
- **下一步**：
  - M1 工单 `DT-ORC-M1-B11-FILE-ISOLATION`：修 B11-R5 指出的文件级符号链接问题；同时按 5.2a/5.2b 实现 `run_authorization`。实现 (b) 时先确认 Worker 环境拿不到常浩的飞书用户身份凭据（N2）。
  - M2 人工直控试点可以和 M1 并行。

## 2026-10-31 上午 · 常浩定自动推进闸门；RELAY-02 循环驱动首次真实跑通（[PR #40](https://github.com/Natsukashii1997/digital-twin/pull/40) 送审中，[PR #39](https://github.com/Natsukashii1997/digital-twin/pull/39) PASS 待合并）

- **常浩 10-31 要求**：digital-twin 基于 ORCA 自动推进到项目结束，不再逐步让他确认。**闸门按建议定**：全自动——开 issue、开分支、Claude 开发、ChatGPT 聊天模式（思考强度「高」）审核、返修（最多 2 轮），以及 PASS 且没碰正式环境的 PR 自动 squash 合并；攒起来用飞书私聊问常浩——正式安装和部署、改 Hermes 正式配置和 cron、机器人对外发消息、NEEDS_HUMAN、返修超限。本条替代 PRD 第 10 节「每个任务开工前给分批计划，你确认后再动」在本项目的执行方式。
- **RELAY-02（#37，PR #40）**：`orca/relay_loop.py` 把任务交给 ORCA 终端里的 Claude → 等就绪文件 → ChatGPT 审核 → PASS 收口 / REWORK 自动交回返修 / NEEDS_HUMAN、超限、出错时用猛鬼兽私聊常浩；中断后重跑即续跑，不重发、不重复通知。relay 发送前自动核对聊天模式，并把强度调到「高」。单测 57 项，12 处反向验证都有测试失败。说明见 `docs/DT-ORC/RELAY-02-自动循环驱动.md`（分支 `claude/37-relay-loop`）。
- **首次真实试运行**：#38（`Marker.bind` 原子写）→ PR #39：第 1 轮 REWORK（GitHub 连接器 404、证据无法独立核实）→ 自动返修 → 第 2 轮 PASS → 飞书通知（常浩已确认收到）。中途 3 次人工介入，都已修到代码或文档里：ORCA 终端的 Claude 要带系统代理 `127.0.0.1:8086` 启动，否则 403；新会话的临时编号 `local-chatgpt%3A…`；续跑 collect 要反复等。
- **权限已解决**：常浩已把 Codex Connector 的仓库访问设为「所有仓库」（ChatGPT 能不能读到，以下一轮审核为准；如果还是 404，查 ChatGPT 设置里 GitHub 连接器连的是不是同一个账号）。本会话的权限模式按常浩要求升到最高（bypassPermissions），合并和强推不再被拦。PR #39 已按闸门变基、复测、squash 合并（`3904910`），#38 关闭。
- **注意**：PR #40 分支要变基到含 `3904910` 的 main。两边都改了 `orca/chatgpt_relay.py`（#39 改 `Marker.bind`，#40 改 `conversation`、`bubble_matches` 和强度核对），变基时核对一下冲突。
- **下一步**：权限放开后合并 PR #39、PR #40（PR #40 审核 PASS 之后），再做第 3 批：任务队列（PASS 合并后自动接下一个任务）和常驻运行（LaunchAgent 属于正式安装，先出方案等常浩点头）。

## 2026-10-31 上午 · RELAY-01 合并（[PR #36](https://github.com/Natsukashii1997/digital-twin/pull/36) squash `5e65524`，#35 关闭）

- **P1-01-R4 定向修复**（常浩批准「批准，新会话修」）：`SEND_JS` 带上核对过的页面地址和送审消息，点击前在同一段页面脚本里核对地址、唯一输入框及内容、没有在生成、按钮可用；不符就不点，清掉发送标记，退出码 2；返回值看不懂时保留标记，按结果不明处理。单测 43 项（新增 3 项，其中一项在 node 里跑真实的 `SEND_JS`），3.9.6 与 3.14.5 都通过；10 处反向验证都有测试失败。真实测试：点击前把标签切到项目首页并填入同样内容，被拒且确实没发出；沙盒长会话完整 review 一次，结果 PASS。证据 `evidence/relay01-20261031/round5-*`。
- **第 5 轮评审 PASS**（思考强度「高」）：P1-01-R4、P2-01-R4、P2-02-R4 关闭，可进入合并审批。新登记两条非阻断 P2：P2-01-R5，输入框比对会去掉空白，不是逐字节一致；P2-02-R5，以后改发送脚本，所有拒绝分支都必须在点击之前。
- **常浩 10-31 接受并说「合并」**：① 远端 HEAD 与送审 diff 一致（送审时 `a6f27d6`，之后只多了评审原文 `73c1306`）；② 正式接受第 3 轮的三项超范围修复（`--chat` 必填、按送审编号认回复、文件错误退出码 2）；③ 第 1 批思考强度靠送审前人工核对（实测切会话后会变回「中」，relay 不检查）；④ relay 运行期间不操作共用浏览器标签，历史 P2 记为技术债。
- **合并过程**：本地 main 与远端一致；分支在本地变基到最新 main 后测试通过，但强推分支被权限拦下，于是直接 squash 合并原分支。main 多出的提交只改 CURRENT，分支没碰这个文件，合并结果和变基后已测的版本一致；合并后在 main 上重跑单测，通过。relay 直接从仓库运行，没有需要同步的安装位置。
- **下一步**：第 2 批要先开 issue，等常浩批准：循环驱动、返修上限（D3-5）、NEEDS_HUMAN 时走 Hermes 飞书私聊通知、在 digital-twin 的一个真实小任务上试运行；可以考虑把「思考强度 高」改成自动核对。还要常浩给 ChatGPT 的 GitHub 连接器开通 digital-twin 读取权限（本轮仍是 404）。

## 2026-10-31 凌晨 · Claude 接管 DT-ORC；RELAY-01 ChatGPT 聊天审核通道沙盒跑通（[PR #36](https://github.com/Natsukashii1997/digital-twin/pull/36) 第 4 轮 NEEDS_HUMAN，待常浩裁决）

- **来由**：常浩说 Codex 额度将尽，由 Claude 正式接管 Hermes 数字分身、Obsidian 知识库和 DT-ORC 的推进。审核方固定为 **ChatGPT 聊天模式**（不是 Work 模式、不是 Codex），要把常浩在 Claude 和 ChatGPT 之间复制粘贴的那一步，改成在 ORCA 里自动往返。常浩确认：接受自动操作 ChatGPT 网页的账号风险；正式项目的审核材料优先走 GitHub PR；ChatGPT 侧新建项目；代码放 digital-twin。
- **做法**：Issue [#35](https://github.com/Natsukashii1997/digital-twin/issues/35)（Part of #32），分支 `claude/35-chatgpt-relay`（worktree `~/Sandbox/worktrees/digital-twin-35-relay`，HEAD `43ab425`，已推送）。新增 `orca/chatgpt_relay.py`：在 ORCA 内嵌浏览器的 ChatGPT 聊天里送审，收回原文并解析结论，REWORK 时交回 ORCA 终端里的 Claude。设计与实测见 [docs/DT-ORC/RELAY-01-ChatGPT聊天审核通道.md](https://github.com/Natsukashii1997/digital-twin/blob/claude/35-chatgpt-relay/docs/DT-ORC/RELAY-01-ChatGPT聊天审核通道.md)。ChatGPT 侧新建了项目「数字分身审核」，项目说明写了审核员规则（已回读）。
- **实测**：沙盒仓库 `~/Sandbox/2026-10-31-orca-relay/sandbox-repo`（已加入 ORCA），全部是合成数据。第 1 轮 REWORK → ORCA 里的 Claude 44 秒返修（`2014c51`）→ 第 2 轮全自动 PASS。中途 5 次失败都是停下，而不是误发或误收，均已修复。13 项单测在 3.9.6 和 3.14.5 下都通过。证据在 `evidence/relay01-20261031/`。
- **没做的**：没在正式项目上运行，没配 ORCA 自动化，没有循环驱动、返修上限和飞书通知。`claude/32-dt-orc`（本地领先 8 个提交、未推送）和 #33、#34 都没有动；ORC-01b-1 R7 原本在等 Codex 审查，往后改由 ChatGPT 聊天审核。
- **PR #36 走自己的通道审核**（10-31 凌晨，后续）：第 1 轮 ChatGPT 判 REWORK（P1×6、P2×4）。返修提交 `1e0bb27`：回复归属与会话核对、稳定窗口、末行结论、发送标记、原子落盘与交付重试、子进程校验；单测 27 项，逐项撤销修复都有测试失败；返修后的沙盒真实端到端 REWORK → 返修（`41b5355`）→ PASS。第 2 轮仍判 REWORK（`cd82bef` 入库），P1-03 末行方案已被接受；剩余 4 条 P1：300 字前缀不足以证明送审包身份、会话校验起点太晚、发送标记绑定输出路径可被绕过、deliver 重试可能让 Claude 重复返修。另有 5 条 P2。按 D3-5，返修上限是 2 轮，这一轮是最后一次。ChatGPT 的 GitHub 连接器仍读不到 digital-twin（需常浩授权）。
- **最后一轮返修**（常浩批准）：`8d69695` 加了送审编号（R-<送审包 sha256 前 16 位>，写在发出消息的第一行）、会话从发送起绑定、按送审包摘要的发送标记（状态目录 `~/Library/Application Support/DigitalTwin/relay/`）、回执首行元数据与完整性校验、交付状态 sending/done（已交付不重发，结果不明要人确认）。单测 34 项；16 处修复逐个撤销，都有测试失败。沙盒真实恢复端到端（合成任务 3）：杀掉后 collect 收回、换 `--out` 重发被拒、交付失败后确认重试、再调用 `already`，Claude 只处理一次，第 2 轮 PASS。
- **第 3 轮评审 NEEDS_HUMAN**（`3fa0cc4` 入库）：上一轮 9 项中 8 项通过，交付幂等部分通过。新提出 2 条 P1，都在交付状态上：① 检查和写入不是原子操作，两个 deliver 并发时可能重复交付；② `already` 不核对目标终端，换终端重试会误报已交付。另有 4 条 P2：新会话绑定写入不是原子操作、标记未绑定会话时 collect 没有明确拒收、摘要只能防篡改不能认证来源、ORCA 原生的 `--retry-request` 恢复协议没有用上。按 D3-5 已到上限，交常浩裁决。ChatGPT 建议批准只针对这两条 P1 的定向修复；如果接受现状，要写明「单进程、单终端、隔离环境」的使用前提。
- **定向修复**（常浩批准破例，`80adf37`）：交付全程持有 flock 进程锁；`already` 核对目标终端。实测中又发现三项并一并修复（超出批准范围，已单列请复核）：① 安全问题——ORCA 浏览器标签是共用的，实测时停在常浩的「分析ORCA协同方案」会话，那次送审只因页面上有两个编辑框才没发出去，于是加了必填的 `--chat` 目标会话，并要求可见输入框恰好一个；② 长会话只渲染最近几轮，消息条数停住，改为按送审编号判断回复是否到达；③ 文件错误改为退出码 2。单测 40 项，22 处修复逐个撤销都有测试失败；真实测试覆盖并发交付、终端核对、错会话拒发。
- **第 4 轮评审 NEEDS_HUMAN**（`43ab425` 入库）：获批的两条 P1 已关闭；三项超范围修复技术上接受。新提出 P1-01-R4：核对目标会话和点发送之间还有时间窗口，标签可能在这中间被切走。ChatGPT 建议批准一次只修这一条（把会话核对放进发送时的同一段页面脚本），之后补长会话的真实完成路径证据，等远端 HEAD 可以核实后再进入合并审批。
- **下一步**：等常浩决定是否批准修 P1-01-R4；PR #36 合并和第 2 批仍待批准。另外需要常浩给 ChatGPT 的 GitHub 连接器开通 digital-twin 的读取权限。

## 2026-10-30 · B2.2 G5 第 0 批与 G5-A 第 1 批：架构定稿，本地通知 Inbox 隔离代码已验收（GO），未合并、未部署

- 时间：10:20–12:20；Issue [#31](https://github.com/Natsukashii1997/digital-twin/issues/31)；分支 `claude/31-g5-delivery`（worktree `~/Sandbox/worktrees/digital-twin-31-g5`，HEAD `b95373d`，**只在本地，没有推送**）。
- **来由**：常浩同意按推荐开工 G5，D8 v1 写成后，又转来 ChatGPT 任务书 V1.0：G5 改成“本地优先、多通道”，飞书只作可选的远程通道。第 0 批（设计）先评审，再按 R1–R5 补正；之后常浩转来《G5-A 第 1 批隔离开发正式开工指令 V1.0》，授权隔离开发。
- **第 0 批**（纯文档，提交 `7c09541`、`933a244`）：现状审计、D8 v2、隔离测试与部署闸门（D1–D6）、G5-A 任务书、补正对照表。审计发现两点：`twin_identity.lark_fetch()` 核实不了 bot 自己发出的消息，所以 D8 v1 的回读方案不成立；`Store` 以读写方式打开时会自动迁移，因此安装新版脚本和迁移正式库合成同一道闸门 D1。
- **G5-A**（提交 `919dd9d` 和返修 `9bacb67`）：
  - `twin_state.py` 追加 v4 迁移：notifications 和 deliveries 两张表；一代一行；旧 `sched.report` 映射成 legacy。
  - 新增 `twin_notify.py`：`sync / list / show / history`，退出码 0–4；只有完整扫描才解决通知；工单目录不见了判为 partial；同一指纹内容变化记新 revision；版本闸门在建锁之前。
  - 新增无参入口 `twin_notify_cron.py`。
  - 第 1 批验收 NO-GO（P1-1、P1-2、P2-1），定向返修后二次验收 **GO**。
- **测试**：新增 58 项，都是先红后绿；全量 `/usr/bin/python3 -B -m unittest discover -s hermes/scripts/tests`（3.9.6）`Ran 335 tests` `OK` exit 0；`python3 …`（3.14.5）同样 335 项 OK。ChatGPT 在本机独立重跑，结果一致。证据在 `~/Library/Application Support/DigitalTwin/evidence/g5a-20261030/`、`g5a-rework-20261030/`；三份评审原文在分支的 `docs/reviews/2026-10-30-ChatGPT-B2.2-G5*`。
- **本轮新坑**：
  - `read_orders()` 遇到读不了的派单目录，可能当成空目录；遇到整张单的目录消失，也不报错（twin_notify 里已自己防住，`read_orders` 本身没改）；
  - `restore()` 恢复完会立刻按当前程序再迁移一次，所以回退到 v3 必须先换回旧脚本再恢复；
  - 只读打开 WAL 库时，SQLite 可能留下空的 `-wal` 文件；
  - WAL 库的副本要用 `immutable=1` 才能只读打开。
- **没做（照实）**：分支没有合并、没有推送；没有安装到 `~/.hermes/scripts/`；正式库仍是 v3（sha `6cde8737…`，前后核对未变）；没有建 cron；没有发飞书；`.env`、Skill、网关都没动；G5-B、G5-C、G6、G7 都没有开始。
- **回退**：生产环境没有变化，不需要回退；要放弃的话，删掉本地分支和 worktree 即可。
- **下一步**：等常浩决定做 G5-B（终端只读层）、G5-C（飞书适配器），还是先做 D1（正式迁移与安装方案）。每一项都要单独授权，建议开新会话来做。ChatGPT 留待后续的项：D1 的迁移与回退要单独验收；G5-C 的幂等键要带上 `content_digest`；工单目录长期消失时，要有需要人确认的收口办法。

---

## 2026-10-30 上午 · B2.2 G4 独立验收：G4 技术验收通过，G3 observe 补验关闭，停在 G4

- 时间：10:15
- **来由**：常浩转来 ChatGPT 的 G4-D 验收包，原文入库：[docs/reviews/2026-10-30-ChatGPT-B2.2-G4独立验收原文.md](docs/reviews/2026-10-30-ChatGPT-B2.2-G4独立验收原文.md)。
- **裁定**：B22-G4 技术验收通过；G3 顺延的运行中 observe 补验关闭；暂不要求 G4-C2。**限制保留**：`observe` 中“状态库已有活动 Run”那条分支（`run` 关联、`heartbeat_age`、异常报告关联）没有在生产运行中验收，转入 G5 前的隔离测试，不得说成“已完整验证”。授权留痕：常浩 10-30 确认 10-29 的 G4 授权「属实」，这项审计已关闭。
- **Claude 复核验收包里的事实**：main = `origin/main` = `b3790cd`；三份备份的 Run 数为 0 / 16 / 17，`integrity_check` 全部 ok；最终备份与现库 `.dump` 0 行差异。唯一出入是 dump 行数：本机 `sqlite3` 导出 100 行，验收写的是 69 行，可能是导出方式不同造成的，不影响“0 差异”的结论。
- **文档**：D6「当前正式状态」与 G4 行、D7 中“版本 2”和固定“15 单”的旧描述已按验收意见修订（纯文档，保留历史背景）。
- 没做（照实）：没开单、没写库，`.env`、技能、SOUL、config、网关、cron 都没动；G5 方案还没开始准备。
- 下一步：准备 G5 技术方案与隔离测试（可信投递闭环 + 活动 Run 分支），先出计划等常浩确认。G5–G7 不做任何正式启用。

---

## 2026-10-30 上午 · B2.2 G4 执行前预检（只读）：现场与 10-29 记录一致，转交 ChatGPT 做 G4-D 验收

- 时间：开工 09:20
- **来由**：常浩转来 ChatGPT「启动 B22-G4 前置准备」指令（只授权预检与分阶段方案，不得建库、对账、开单、改配置）。指令的前提是正式库未建，与现场不符：G4-B / C 已在 10-29 执行（上一条）。看过预检结论后，常浩说「转交 ChatGPT 验收」「提交并记 CURRENT」。
- **做了什么**（全部只读，读库在会话临时目录的副本上做）：main = `origin/main` = `3f9aa23`，`b4c350c` 之后没有代码改动；`hermes/scripts/*.py` 12 个加 `self_check.py`、`check_repos.py` 与安装副本逐字节一致；安装版自检静默；正式库 schema v3、`integrity_check` ok，Run 17 条（done 5、cleaned 8、abandoned 4），其余表除 events 34 外都为 0；17 个 `order.json` 与 17 条 Run 单号、状态逐一相同，10-29 之后没有新单；三份备份 sha 与上一条一致，最终备份与现库 `.dump` 0 行差异；`.env` 与网关环境没有 `DT_*`，`owner_chat_id` 不存在，cron 只有日报、周报、自检。
- **产出**：[docs/B2.2-G4执行前预检与分阶段施工方案.md](docs/B2.2-G4执行前预检与分阶段施工方案.md)（G4-A 至 G4-D 各阶段的前置条件、命令、预期、风险、回退与授权，另有可选补测 G4-C2）；验收材料包（转交说明、方案、上一条原文、D6、observe 源码片段、10-29 原始证据）已发给常浩转交 ChatGPT。
- **待 ChatGPT 判断的覆盖缺口**：10-29 观测单跑单时正式库里还没有它的 Run，16 次 `observe` 都走“只在台账中 running”的分支（`run = null`），“库内已有活动 Run”的分支没有在正式库上测到；两条分支用同一个 `vital_of` 核对身份。是否补测（G4-C2：再开一张只读单，跑单中途对账后观测）由 ChatGPT 判断，补测前须常浩另批。
- 测试：未改代码，未跑测试。
- 没做（照实）：未写库、未对账、未开单，`.env`、技能、SOUL、config、网关、cron 都没动。文档漂移留待验收后一并改：D6「当前正式状态」一行、D7「库版本 2」与「预期 15 单」。
- 下一步：等 ChatGPT 对 G4 的独立验收；它要求补测就先在隔离库上预演 G4-C2，再请常浩批准正式执行。G5–G7 仍关闭。

---

## 2026-10-29 晚 · B2.2 正式库接入与运行中体征验证（G4）：正式库已建、三次对账、观测单 alive，调度仍关闭

- 时间：开工 23:30
- **来由**：常浩说「批准 G4：建正式库（init、backup、两次 reconcile）和一张只读观测单」，看过施工计划后「按推荐开工」：观测单跑完再对账一次导入它；D6 的 G4 行直接提交 main。G5–G7 未批。
- **做了什么**（全部用 `/usr/bin/python3` 与安装版脚本；SQLite 3.54.0）：
  - 开工前：四个脚本与 main 一致、自检静默；正式库与 `backups/` 都不存在（直接建 v3，无迁移）；没有活动单与相关进程；`.env` 无 `DT_SCHED_ENABLE` / `DT_STATE_NOTIFY`；cron 只有原有日报、周报、09:50 自检。先在会话临时目录的隔离库上用真实派单目录预演，得到预期数。
  - `twin_state.py init` → `~/Library/Application Support/DigitalTwin/twin-state.db`，schema v3、WAL，库文件 600、目录 700；随即 `backup`（`backups/twin-state-20261029-233242.db`，sha256 `0b19c82f1c85…`）。
  - `reconcile --dispatch ~/.hermes/dispatch` 两次：第一次补上 16 单（created 16）、3 个没有 order.json 的目录 ignored、0 冲突；第二次 16 单 unchanged、created 0、0 冲突，与预演一致。之后再 `backup`（`…-233254.db`，`5380b8dba315…`）。库里 16 条 Run（abandoned 4、cleaned 8、done 4），目标 / 任务 / 预留均为 0。
  - 观测单 `D20261029-2`（数字分身 · claude · 只读，任务同 G3）：脚本后台跑单，跑单期间每秒只读 `twin_scheduler.py observe` 正式库，约 20 秒内 16 次全部返回该单、`vital = alive`（“车间进程在，身份都对上”），`worker` / `dispatch` 核对均为 `match`，车间 pid `98740` 与 `worker.json` 的 `pid = pgid = sid` 一致；跑单 `done`、退出码 0，回执 `head_before = head_after = 4949a4c`、无提交与改动。跑完再观测：“没有活动的单”。
  - 第三次 `reconcile`：补上 1 单（观测单）、16 单 unchanged、0 冲突；`backup`（`…-233421.db`，`1648fb25930e…`）。库里 17 条 Run。`plan`：没有未收尾的任务；`queue`：8 个分桶全为 0。
  - 只读核对：`observe / plan / queue` 之后库文件 sha 变了（SQLite 页头 / 检查点），但与第三次对账后的备份逐行导出比对逻辑差异 0 行。
  - D6 的 G4 行补上“首项必测”。
- 测试：未改代码，未跑单元测试；验证即上面的命令输出。证据 `~/Library/Application Support/DigitalTwin/evidence/b2.2-g4-20261029/`（`00-precheck.txt` 至 `13-queue.json`、`g4_observe_run.py`、`task.md`）。
- 本轮新坑：无。（事实：WAL 库的 `-shm` 由 SQLite 按 644 建，靠所在目录 700 挡住别的用户；对 WAL 库做的备份文件也带 WAL 标记，用 `mode=ro` 直接打开会因建不了 `-shm` 失败，核对时先复制再开。）
- 没做（照实）：`DT_STATE_NOTIFY` 未开（手工派单不写库，库靠手动对账更新）、`DT_SCHED_ENABLE` 未开、没建 cron、没自动开单、没自动确认报告、没发真实取消信号；`owner_chat_id` 未配；网关 / 技能 / SOUL / config 未改。D6 第一张表“当前正式状态”一行仍是 B2.1 时的描述，以本文件为准。
- 回退：删除 `twin-state.db`、`-wal`、`-shm` 与 `backups/` 回到无库；或 `twin_state.py restore --from <备份> --yes`。派单目录未受影响。
- 下一步：G4 执行记录交 ChatGPT 复核；通过后 G5（报告投递，须可信送达确认与端到端测试）待常浩单独批准，G6、G7 仍关闭。

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

## 2026-11-12 · 审计第 1 项：凭据扫描读取失败即停（仅隔离 worktree，未提交、未安装）

- **已核实并最小修复**：当前基线 `37af134` 的 `dispatch.secret_hits()` 在 `git log -p` 非零退出时仍按空结果放行。现改为返回 `ok=false`、退出码 1，停止后续推送；错误只报退出码，不转发 Git 原始输出。
- **回归与全量**：新增 2 项测试、3 个场景，覆盖开 PR 时空输出 / 部分输出失败、确认推本地主分支时读取失败；修前均失败，修后通过，并核对未调用 push、未继续 PR 操作、台账不变。系统 Python 3.9.6 全量：Hermes 767 项，17 失败、4 错误（沙箱禁止 `ps` 和本地套接字，未通过）；ORCA 48 项通过。将这 21 项在未修改的 `37af134` 源码隔离副本上定点重跑，复现相同的 17 失败、4 错误；不将环境受阻写成全量通过。
- **证据与边界**：`~/Library/Application Support/digital-twin/evidence/2026-11-12-codex-fix/summary.md`；本轮仅修审计第 1 项，不处理其余项。按本轮明确要求不提交、不推送、不联网、不安装、不改真实数据或运行配置；项目原有主线和已定内容不变。

## 2026-11-12 · PR #171 交叉审查返修：补齐合并入口凭据扫描（仅本 worktree，未提交、补改未复审、未安装）

- **审查发现成立并已补齐**：起始 HEAD `4a04971508604872d85104567b61d1b5abbc84da`、分支 `codex/audit-fix-1112`。已有 PR 续跑后，`pr` 虽已拦下扫描读取失败或凭据命中，`merge_github()` 仍能直接推派单分支。现于变基、项目测试之后、分支推送之前，对 `refs/remotes/origin/main..HEAD` 调同一个 `secret_hits()`；读取失败或命中即停，不推派单分支、不调 GitHub 合并、不改完成台账，沿用脱敏回报。
- **先红后绿**：新增 3 项测试、5 个场景（128 空 / 部分输出、-9、真实合成凭据、真实变基后且测试完成再扫描）。只加测试时 `Ran 3 tests` / `FAILED (failures=5)`；修后 `Ran 3 tests` / `OK`；派单 GitHub 文件全部 20 项通过。
- **本轮全量**：`/usr/bin/python3 -B -m unittest discover -s hermes/scripts/tests`：770 项，17 失败、4 错误，**未通过**；ORCA 同解释器全量 48 项通过。21 个失败 / 错误名称及类别与起始 HEAD 的隔离定点复跑完全一致（17 失败、4 错误），原因涉及沙箱禁止 `ps` 与本地套接字；未跳过、改弱测试或声称全绿，未启动浏览器。
- **交接与范围**：`~/Library/Application Support/digital-twin/evidence/2026-11-12-codex-fix/summary.md` 末尾「交叉审查返修」，原样日志及起始版本对照在同目录 `rework/`。只修改本 worktree 的 `dispatch.py`、`test_dispatch_github.py`、本文件；未提交、未推送、未联网、未同步安装副本或改运行配置。本轮无须确认的已定内容变化；原审计另 5 项中危不在本报告具体发现中，未重新核实或修复，不宣称解决。
- **唯一下一步**：在允许 `ps` 和本地套接字的本机环境，对此未提交候选版重跑 Hermes 与 ORCA 两条全量命令，回填证据后交回 PR #171 复核；本轮不放行合并或安装，项目整体仍处原阶段。
