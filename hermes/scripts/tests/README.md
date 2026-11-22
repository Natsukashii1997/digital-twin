# hermes/scripts 自测

名册里 digital-twin 的 test 就是这条命令，合并前会自动跑：`/usr/bin/python3 -B -m unittest discover -s hermes/scripts/tests`。一律用系统自带的 `/usr/bin/python3`（3.9，和 Hermes 定时任务、CI 同版本）：3.10 以后的写法（如 `fromisoformat` 认结尾的 Z）在 3.9 上会失败，本机 3.14 全过不算数。用别的版本跑，输出最后会提示一句（`test_python_version.py`），不判失败。

## 统一测试隔离（_isolation，#212）

测试碰不到本机正式环境，不靠每个测试自己记得隔离（来由：11-17 反向验证误装进正式中台、10-27 假模型名写进正式凭证 G-13、11-11 覆盖正式配置）：

- **每个测试模块第一句（文档字符串之后）写 `import _isolation`**，在导入任何被测脚本之前。`test_isolation_guard.py` 扫描本目录，漏写或写在别的 import 后面就失败。按上面的命令跑时 `_isolation` 包排在所有 `test_*.py` 前面先被导入，漏写的模块也已在隔离里；单跑一个文件靠第一句。
- 导入时：清掉 `DT_*`、`HERMES_*`、`FEISHU_*` 等可能指向正式环境的变量（`DT_HUB_E2E`、`DT_AUTOLOOP_CLI` 两个开关保留），把 `HOME`、`XDG_*`、`AGENT_AUTOLOOP_HOME`、`HERMES_HOME`、`CODEX_HOME`、`CLAUDE_CONFIG_DIR`、`DT_GRANTS_STATUS`、`DT_GRANTS_TASKS`、`DT_AUTOLOOP_SHARED`、`DT_PUBLISH_OUTBOX` 指到本次运行的临时目录（要只读核对真正的正式目录、读上游源码，用 `_isolation.REAL_HOME`，不用 `~`），`DT_HUB` 指到不监听的 127.0.0.1:9；git 用临时全局配置。
- 默认禁网：本进程连非回环地址、解析外部域名抛 `_isolation.BlockedNetwork`（`OSError` 子类）；回环放行，但本机正式服务端口（3350、3352、5433、8085、8086、8090）也拦。子进程拿到指向死端口的 `HTTP(S)_PROXY`。
- `PATH` 最前面放拦截桩：`launchctl`、`security`、`sudo`、`crontab`、`osascript`、`pfctl`、`lark-cli`、`hermes`、`claude`、`codex`、`gh`、`multica`、`docker` 调到就退出码 97。测试要用假命令，照旧把自己的目录放在 `PATH` 更前面或用 `DT_*_CLI` 指定。
- 进程退出时把拦下的连接和命令汇总打到 stderr。缺口：测试给子进程拼全新环境（如 `PATH` 只写 `/usr/bin:/bin`、不带 `HOME`）时桩和代理管不到那个子进程，这种写法要自己指好临时目录。

## 反向验证核对正式目录（prod_snapshot.py，#212）

「故意改坏」类验证和全量测试的前后，用它核对本机正式目录没被动过。只读：只取路径、类型、大小、修改时间，不打开文件；家目录按系统账号取（不受 `HOME` 影响）。默认核对 `~/.agent-autoloop`、`~/.hermes`、`~/Library/LaunchAgents`、`~/Library/Application Support/DigitalTwin`、`/Users/Shared/dt-autoloop`，变了退出码 3 并逐条列出新增 / 删除 / 变了的路径。

```sh
python3 -B hermes/scripts/tests/prod_snapshot.py run --out-dir <证据目录> -- python3 -B -m unittest discover -s hermes/scripts/tests
python3 -B hermes/scripts/tests/prod_snapshot.py take 前.json     # 之后 check 前.json 再比；diff 前.json 后.json 比两份
```

改坏脚本里每改坏一处、跑完测试就核一次，变了立刻停：

```python
import prod_snapshot as ps          # sys.path 加上 hermes/scripts/tests
base = ps.take()
...                                 # 改坏一处、跑测试、还原
ps.assert_unchanged(base)           # 变了抛 ps.ProdChanged，消息里写明哪些路径变了
```

Hermes 网关一直在写心跳和运行状态（`~/.hermes/state/`、`gateway_state.json`、`*.db-wal` 等），空闲时也会变；要排除就加 `--exclude '<绝对路径通配>'`，排除了什么写进证据。

## 派单续跑回执（test_dispatch_followup.py）

只需要 Python 3.8 或以上版本的标准库，无需安装依赖。在仓库根目录执行：

```sh
/usr/bin/python3 -B -m unittest discover -s hermes/scripts/tests -v
```

也可以直接运行脚本：

```sh
/usr/bin/python3 -B hermes/scripts/tests/test_dispatch_followup.py
```

预期：全部测试均显示 `ok`，末尾显示 `Ran N tests in ...s` 和 `OK`，退出码为 0。总数随新增测试变化，这里不写死；交接里写实际跑出的 `Ran N tests`，和上一次交接比，少了要查原因。耗时随机器变化。`-B` 禁止生成字节码缓存。

测试会调用源文件 `dispatch.py` 的命令行入口，解析 `run` / `--followup` 参数。每项测试使用独立临时目录中的虚构台账、任务和回执，把派单根目录、worktree 根目录、worker 路径、名册路径全部替换为临时路径；`subprocess.run` 由假 worker 接管，并检查命令和输出目录。假 worker 像真实 worker 一样原位重写 `receipt.json`，因此能检出把原回执直接硬链接成历史文件的错误。测试结束自动清理临时数据，不调用真实模型、外部服务、Git 或安装文件，不接触真实派单。

覆盖范围：

- 首次 followup：在 worker 被调用前，`receipt-1.json` 已逐字节保留首轮回执，`receipt.json` 仍是原内容；原 `session_id` 正确传给 `--resume`。
- 连续三次 followup：生成 `receipt-1.json` 至 `receipt-3.json`，各轮内容保持不变，`receipt.json` 更新为第四轮。
- 已有历史：按最大编号加一，支持编号空缺和两位数；已占用的文件、目录和悬空链接均不覆盖，无关名称保持不变。
- 发布冲突：扫描编号后、发布前出现同名历史文件，返回失败，保留对方文件和原回执，不启动 worker。
- 失败停止：回执读取失败、非法 JSON、非对象 JSON、无效编码、归档创建失败、部分写入失败、磁盘同步失败及发布失败，均返回 `ok: false`、退出码 1；worker 未调用，台账及原回执不变，正常清理本次临时副本。
- 路径异常：当前 `receipt.json` 为目录或符号链接（包括悬空链接）时拒绝续跑，不改链接目标。
- 兼容行为：普通非 followup 运行不归档、不传 `--resume`；确实没有旧回执时，followup 沿用原有的新会话启动行为；回执的 `session_id` 为 null 时仍归档，再无 `--resume` 启动。已处于 running 的单仍被拒绝。

实现只保留 JSON 回执，不归档 `stdout.log`、`stderr.log` 等其他输出。历史使用独立临时副本完整写入并同步后发布；发布路径已被占用就停止，不覆盖、不自动重试。本次没有改动整单并发调度机制，也未验证断电恢复或真实模型续跑。

修改前的首次回归结果为 4 项中 3 项因历史文件不存在而报错，普通非 followup 测试通过；修改后以上 13 项全部通过。2026-10-26 已合并（`a8b4503`）并同步到 `~/.hermes/scripts/`。

## 项目进展取数（test_progress_data.py）

用临时 git 仓库、临时名册（`DT_ROSTER`）和临时台账（`DT_DISPATCH`）跑 `progress_data.py --date 2000-01-02`，不碰真实仓库和 `~/.hermes`。覆盖：当天提交按分支归、主分支优先、同一提交不重复、别的日子不算；CURRENT“下一步”的三种写法（列表、表格行、标题）且不误认说明文字；没动静的项目并成一行；只列当天的派单；读不了的仓库只标该项目；`--project` 按别名筛选、找不到报「取数失败」；名册 `inventory` 里的盘点仓库不现查；名册坏了退出码 1。

## 派单接 GitHub（test_dispatch_github.py）

用临时裸仓库当 GitHub 背后的远端、临时主仓库、临时名册和台账；PATH 最前面放假 `gh`（`fake_gh.py`，不联网，按状态目录里的文件回话、记下每次调用，`pr merge` 在裸仓库上做 squash 合并并删分支）。git 用临时全局配置，不读本机的。覆盖：

- `new`：写入单从 origin 的主分支拉分支，本地主分支没推的提交不进本单；建 issue（标题取“目标”、带 `ready-for-agent`、正文含五要素）；只读单不调 gh；issue 建不成照常开单并在 `msg` 说明。
- `pr`：只推本单分支（本地主分支没推的提交不上去），PR 正文 `Closes #7`、车间总结、提交的文件；提交里像有密钥时不推、`msg` 不带出密钥本身。
- `merge`：本地主分支有没推的提交时先列出、不合并；`--push-main` 确认后推主分支、变基、squash 合并、主目录拉齐；确认之后本地又有新提交时拒绝；分叉时停；测试不过不合并；GitHub 已合并但主目录有重叠的未提交改动时报 `synced: false`，不动这些改动。
- `clean`：PR 不是 MERGED 时不清理；确认 MERGED 后 `branch -D`；放弃时关 PR、关 issue、删远端分支。
- 车间远端护栏（`run_worker.remote_guard`）：写入单只有本单分支在远端出现或变化才算违规，其他远端引用变化只记 note；只读单不算违规。

## 进展里的 PR 和 issue（test_progress_github.py）

假 `gh` 喂固定的 PR 和 issue，时区固定北京时间。覆盖：按北京时间算当天的 PR 新开、合并、未合并关闭和 issue 新开、关闭，前后一天的不算；只有 GitHub 上有动静的项目也列出来；gh 连不上时只在该项目下写「GitHub 取不到」，没动静的项目在「今天没动静」里带标记，连不上一次后不再逐个等超时；名册没有 `github` 的项目不调 gh。

## 每日巡检（test_health_check.py）

用临时目录造假的 `state.db` 和 `agent.log` 检查主对话 / 辅助任务回退、oneshot 排除、日期过滤、日志计数和缺失日志处理（6 项）。另 3 项固定日期、临时日志目录，在 `subprocess.run` 层拦住 `lark-cli`：日常异常发一条、沿用 bot 身份和私聊、幂等键 `health-日期 时间`；`--dry-run` 只打印不发；1 号也不再查沙盒和仓库（每月部分已搬到 `self_check.py`）。不联网、不碰 `~/.hermes`、不真的发消息。

单跑：`python3 -B -m unittest discover -s hermes/scripts/tests -p test_health_check.py -v`。

## 工程自检（test_self_check.py）

测的是 `ops/self_check.py`。漂移检查跑真实入口：临时 git 仓库当 `DT_REPO`，临时目录当 `HERMES_HOME`、`DT_BIN` 和 `HOME`，`DT_TODAY` 固定日期；git 用临时全局配置。覆盖：

- 漂移：全部一致时无输出；安装缺文件、内容不同只报路径不报内容；`check_repos.py` 两处安装都查；`hermes/scripts/assets/` 下的素材也查；`hermes/scripts/tests/`、其余非 `.py` 文件、安装处多出的文件不管；工作区未提交改动和别的分支的提交不算漂移；仓库不在或不是 git 仓库记一条「取不到仓库 main」。
- 日期：非 1 号只查漂移；1 号或 `--monthly` 才查沙盒和仓库；端到端用空名册跑真的 `check_repos.py`，名册读不了报失败项。
- 沙盒（从巡检迁来）：只算顶层、非隐藏、超过 30 天没动的；目录扫不了记一条「扫不了」，不崩。文案和「另有 N 项」截断不变。
- 仓库自检（从巡检迁来，打桩 `subprocess.run`）：只返回失败项，全通过或只有提示返回空；用当前 Python 和同目录 `check_repos.py`、继承 `DT_ROSTER`、600 秒超时；空输出、损坏 / 不一致的 JSON、意外退出码、启动失败和超时都记「仓库自检未完成」，不带出 traceback。

不联网，不碰真实仓库、`~/.hermes`、安装副本和 Vault。单跑：`python3 -B -m unittest discover -s hermes/scripts/tests -p test_self_check.py -v`。

## AI HOT 热点榜推送（test_aihot_hot.py）

测的是 `hermes/scripts/aihot_hot.py`，跑真实入口。接口用 `file://` 假数据（`DT_AIHOT_BASE`），假 `lark-cli` 按预设回话应答并记下每次调用的参数、工作目录和环境；记录目录、横幅都在临时目录，`DT_NOW` 固定时间。覆盖：正常发送时没有表头（横幅即标题，预览文字在 config.summary）、横幅在最前、名次栏样式（前三灰底奖牌红字、4–10 灰色名次、话题标签、标题只链 aihot.news、不出来源 / 参与者 / 省略号）、bot 身份、去掉 `HERMES_*`、成功不输出、记下当天卡片 / 状态 / 运行记录；概括按整句截取、首句超长按分句收成一句；同一天只发一次、`--force` 换幂等键、横幅按内容缓存、换图才重传；接口取不到、格式不对、不到 10 条、榜单超过 48 小时没更新都输出一句原因、退出码 1、不发卡片；坏链接（javascript、ftp、别的域名、带括号）和注入文字（`<at>`、`<font>`、markdown 链接）被拦或转义；单条详情取不到那条只有标题；横幅上传失败照发不带图并提醒；发送失败、回读（核对底部固定一行字）对不上报原因，已发出不重发；没有私聊 chat_id 报错、环境变量里没有时从 `~/.hermes/.env`（`DT_HERMES_ENV`）读（#165）；`--dry-run` 只打印。不联网、不碰真实记录和 `~/.hermes`。单跑：`python3 -B -m unittest discover -s hermes/scripts/tests -p test_aihot_hot.py -v`。

## Tidings 周报推送（test_tidings_weekly.py）

测的是 `hermes/scripts/tidings_weekly.py`，跑真实入口。假 `lark-cli`（`DT_LARK_CLI`）按预设回话应答并记下每次调用的参数、工作目录和环境；周报目录、状态文件、横幅都在临时目录，`DT_NOW` 固定时间。覆盖：正常发送时横幅插在最前面、bot 身份、去掉 `HERMES_*` 再调、成功不输出；同一期只发一次、横幅按内容缓存、换图才重传、`--force` 换幂等键再发；没有周报或数据过期只发一句说明且同天不重复；卡片为空、不是卡片、缺失都发说明；横幅上传失败照发不带图并提醒；发送失败退出码 1、下次再发、已上传的横幅缓存留着；发出后状态写不进不报失败只提醒；没有私聊 chat_id 报错不发、环境变量里没有时从 `~/.hermes/.env` 读（#165）；周报时间不早于每日更新、旧的 11:30 按和 github-center `readSettings` 同一条规则迁到每日更新后 1.5 小时（github-center #83）；`--dry-run` 只打印。不联网、不碰真实周报、状态文件和 `~/.hermes`。单跑：`python3 -B -m unittest discover -s hermes/scripts/tests -p test_tidings_weekly.py -v`。

## 仓库自检（test_check_repos.py）

测的是 `ops/check_repos.py`。每个项目一个临时裸仓库当远端、一个临时主目录，临时名册（`DT_ROSTER`）；假 `gh` 从状态目录的 `api.json` 回 `gh api` 的仓库设置、标签和文件内容，路径不在里面按 404；`fail_match` 让只含某个仓库名的调用失败。git 用临时全局配置。覆盖：全部合规时 `ok: true`、退出码 0，没有 `github` 的项目不查，跑完本地引用不变（不 fetch）；远端不私有、默认分支不对、rebase 没关、不自动删分支、Issues 没开、缺标签、没有 PR 模板、入口文档没有「工程流程」逐项记失败，名册 `test` 为空只记提示；主目录不在主分支记失败且不切分支；本地主分支多出没推的提交记失败，远端有本地没有的提交记「本地落后，需拉取」提示；某个项目 gh 连不上只把该项目标失败、不再逐项调 gh，其他项目照查。

## 日报周报取数和写回（test_report.py）

PATH 不动，用 `DT_LARK_CLI` 指向假 `lark-cli`（`fake_lark.py`，不联网，按临时目录里的 state.json 回话，`docs +update` 会真的改假文档以便回读）；项目进展、台账种子、证据目录都换成临时路径。覆盖：私聊全收、和猛鬼兽的私聊不收、自己给自己的备忘收、群只收常浩发言或被 @ 的；飞书任务只收常浩负责、近期到期或当天新建的；前一篇台账优先于种子；同一天已写过、周末没动静输出 SKIP；lark-cli 失败退出码 1；周报只取本周日报、按日期排；写回插在说明段之后、同一标题不重写、星期 / 缺段 / 表格 / 标题格式 / 周号不对时拒绝且不调用写入。

## 持久状态底座（test_twin_state.py）

测的是 `twin_state.py`（B1：Goal / Task / Run / Event / Ref）。每项测试用独立临时目录里的状态库（`Store(path, clock=…, dispatch_dir=…)`，固定时钟 2000-01-01 09:00+08:00，派单台账指向临时目录、默认不存在）和虚构的工单；不碰 `~/.hermes`、真实工单、安装副本，不调用模型或外部服务。覆盖：

- 任务书 B1.5 十项验收，按编号 `test_01` ～ `test_10`：建目标与任务（含不能跨目标依赖）；未授权不能进 ready / 不能开跑、Hermes 不能授权、目标撤销连带任务；一个任务多次 Run（失败 → 重试 → 成功，再挂一张既有工单）；Run 成功只到 review，merged / cleaned 不动任务，目标完成只有常浩能定；同一幂等键的开跑 / 回执 / 通用事件只处理一次（`dup_count` 计数），终态后的不同回执被拒；关库重开状态不变，没有台账的 Run 超时标 stale、任务转技术阻塞，迟到结果只记事件，依赖完成自动放行，running 却没 Run 的任务转阻塞，handler 中途抛异常整体回滚、同键可重来；失败 / 超时 / 护栏 / need_decision 各自的阻塞类型和原因；decision 类阻塞对 hermes / claude / codex / system 的重试与解阻塞一律拒绝且留痕，technical 重试与解阻塞共用上限；外部探针抛异常或返回空只记 `ref.unreachable`、任务不动，台账缺失时恢复不下判断，没有验收结论不能完成；台账同步只建未关联的 Run、不建目标和任务，坏文件跳过、重同步无变化、关联后台账跑完才反映到任务、台账跳状态也跟得上、目录不存在记“缺失”、旧台账文件原样。
- 2026-10-29 第二轮评审的收口项（`FinalFixTests`，每项先在第二轮评审对象 `2e496f1` 上确认会失败）：F1 台账把 cleaned / guard / done 的工单改回 running / queued 且带来不同回执时，Run 的状态、回执、证据、会话、结束时间一个都不变，任务不动，`run.conflict` 事件带台账状态和有差异的回执 / 证据字段，同一状态下台账内容变了照常刷新；F2 decision 阻塞、已取消、已完成的任务和不是当前 Run 的旧 Run 主动续跑一律拒绝（Run 不变、没有 followup 事件，经事件入口时拒绝留痕），常浩本人可以，台账里实际发生的续跑把 Run 照实记 running、任务不动、记 `run.resume_conflict` 并计入 `conflicts`，常浩解开后台账跑完照常结算；F3 任务正由 B 执行时后导入并关联更早跑完的旧工单，当前 Run 仍是 B（`current_run_id`），旧工单的迟到结果只记 `run.late_result`，只挂过已结束 Run 的任务回退到最近开始的那个，关联进行中的 Run 才改归属；F4 源码里没有写死的 open_id，`owner_open_id()` 先读 `DT_OWNER_OPEN_ID` 再读 `DT_OWNER_FILE`（取第一个非空行，权限不是 600 不认），命令行没配置时 `--by user` 一律拒绝并提示怎么配，配好后只认配置里的那个；B3 审查 Run（`start_run(purpose="review")`）只对 review 里的任务开、审查方不能是执行方、任务状态不变、不成为当前 Run、一任务一活动 Run 不变，回执只记 `run.review_result` 不结算任务，结论由审查方引用审查 Run 写，审查失败或续跑也不碰任务。
- 2026-10-29 独立评审返修项（`ReviewFixTests`，每项先在返修前的模块上确认会失败）：R1 decision 阻塞不能被非 user 改成别的类型再重试、非 user 只能升级成 decision、已阻塞任务改类型也查类型和原因；R2 过期的旧 Run 迟到成功 / 失败只改它自己，经台账续跑和跑完也一样，任务仍由当前 Run 推进；R3 空回执、缺字段、exit_code 不是整数都算失败并写明原因，只有 PR 引用或成功 Run 不等于验收通过，验收结论必须写依据并引用本任务证据、执行方不能自验、验收不通过退回 ready、续跑清掉结论、当前 Run 还在跑不能完成；R4 进行中的 Run 只能挂到 ready 且目标已授权的任务（observed / review / running / 终态都拒绝，项目对不上拒绝，不形成两个活动 Run），已结束的 Run 不能挂到终态任务，撤销任务 / 目标只记“已请求取消”、Run 仍 running 并可按 `cancel_pending` 查到，执行层真正停下后结果只记事件；幂等键重复新建目标 / 任务返回既有对象；台账回退、护栏降级、放弃后又完成记 `run.conflict` 不应用，done 之后续跑是 followup，done → cleaned 是允许的跳过；续跑规则（review / technical 阻塞可续，failed 续跑计一次重试，到上限或 decision 阻塞时主动续跑直接拒绝、Run 不动，guard 续跑只有常浩能定）；恢复先看台账（台账说在跑只列 suspect、台账给结果就结算、台账读不了记 unknown）；回执白名单。
- 其余：非法转换与绕过状态机改状态；撤销规则；返工、心跳、引用；备份同秒不覆盖、恢复前完整性 / 版本检查、库版本护栏；库文件 600、目录 700；命令行往返（常浩的 open_id 从临时的 `DT_OWNER_FILE` 文件来，`--by user` 必须带 `--message-id`（或 `HERMES_SESSION_MESSAGE_ID`）指向常浩本人最近在私聊里发的一条消息，由假 lark-cli（`DT_LARK_CLI`）回读核实，否则 `NotAuthorized`；一条消息只换一次动作，命令没成功时占用退回；init、goal-new、task-new、task-ready、run-start 带幂等键及重复、run-receipt、ref-add、run-start --purpose review 与审查回执、task-accept、task-complete、show、list、events、goal-complete、queue、reconcile、recover、backup、restore --yes）以及拒绝时 `ok: false`、退出码 1；输出里不带任何 open_id / chat_id 值。
- B2.1（`IdentityStoreTests`、`QueueTests`）：v1 库打开自动迁到 v2（多一张 `identity_uses`），消息占用一次、退回后可再用；调度消费查询 `queue()` 的分桶——ready / waiting_deps（依赖未完成）/ blocked_decision / blocked_external / blocked_technical（retryable 与 needs_human 按重试上限分）/ cancel_pending / review（带有无回执与结论）/ unsafe（外部引用不可达、当前 Run 在台账找不到或台账缺失、台账冲突没处理），查询不改状态。

系统 `/usr/bin/python3`（3.9.6）和 `python3`（3.14）都要能通过：模块只用 3.9 有的语法和标准库。单跑：`python3 -B -m unittest discover -s hermes/scripts/tests -p test_twin_state.py -v`。

## 飞书可信身份桥接（test_twin_identity.py）

测的是 `twin_identity.py`（B2.1 T03）。纯函数 `verify_message` 用注入的回读结果和固定时钟；`lark_fetch` 打桩 `subprocess.run`；命令行走假 lark-cli（`fake_lark.py` 的 `im +messages-mget`）和临时的 600 配置文件。覆盖：常浩本人、私聊、时效内才通过；别人、应用消息、群聊或没有会话信息、没配 `owner_chat_id`、过时效、未来时间、已撤回、回读失败、查不到、message_id 格式不对、没配 owner 一律拒绝且输出不带身份值；分钟级时间与偏差；回读固定用猛鬼兽 profile 的 bot 身份并去掉 HERMES / OPENCLAW 环境变量；`verify` 与 `setup-chat`（预览不写、`--write` 写 600 文件、别人的消息不认）。2026-10-29 评审返修加：p2p 标志不能绕过 `owner_chat_id`、chat_id 不同拒、回读 message_id 不一致拒、`sender_type` 缺失拒、话题群拒、真实 mget 形状（无会话类型）配好 chat_id 通过；`lark_fetch` 回读不止一条 / 不是这条 / 字段不全抛错；`lark_chat_mode` 用 bot 身份裸 API；`setup-chat` 群聊拒、拿不到会话类型 fail closed、写入失败保留原文件且不留临时文件。26 项。

## 统一事件接入（test_twin_events.py）

测的是 `twin_events.py`（B2.1 T02）。临时库、固定时钟、注入的飞书回读。覆盖：信封缺字段 / 来源与角色不配 / 正文当身份都拒；正文只留 sha1，信封自带的 verified / open_id 丢掉；user 事件经核实才应用并占用消息，别人、群聊、查不到的消息拒绝并留痕（applied=0）；一条消息只换一次动作，重复事件不再回读飞书、只计 dup_count；被 Store 拒绝的动作不消耗消息；Agent 回执重复只应用一次并记引用；旧 Run 的迟到回执只记 `run.late_result`；台账回退是冲突、证据不变；`order.sync` 用内容哈希键（同秒连写不漏）、没有 order.json 的目录只计 ignored、对账补漏；note 只留痕；Store 的授权规则经信封照常生效；命令行 ingest / validate / types。2026-10-29 评审返修加：`identity / payload / refs` 非对象给 EventError；临时失败（配置缺失、回读失败 / 出错）不记事件、同一 event_id 重试成功；永久拒绝（别人、Store 拒绝）锁住 event_id，再来返回 `rejected_before`、不回读；`order.sync` 越界 / 符号链接 / 单号不匹配拒绝，合法目录与 `notify_order(dispatch_dir=…)` 照常；命令行三种文案。26 项。

## 派单状态通知（test_dispatch_notify.py）

测的是 `dispatch.py` 的 `notify_state`（B2.1 T04）与 `twin_state.reconcile`。和派单续跑测试一样加载源文件、替换根目录和 worker，`NOTIFY_LOG` 指向临时文件。覆盖：`DT_STATE_NOTIFY` 不设时什么都不碰；开着但状态库不存在绝不建库、只记日志；库存在时只同步这一张单、成功不记日志；通知抛错不影响派单、`order.json` 照写、日志留一行；经 `run` 命令整段 stdout 仍是一个 JSON；重复通知幂等；关掉通知时漏掉的变化由 `reconcile` 补上；台账冲突记日志、证据不动；need_decision 之后“先由 user 解阻塞再续跑”正常、顺序反了只记 `run.resume_conflict`。10 项。

## 持久调度（test_twin_scheduler.py、test_twin_scheduler_life.py、test_twin_scheduler_e2e.py）

测的是 `twin_scheduler.py` 与库 v3 的调度预留（B2.2，设计见 `docs/B2.2-调度与恢复机制设计.md`）。临时库、固定时钟、临时派单根目录；需要真实进程的用 `dispatch_harness.py`（在临时根目录里跑真实 `dispatch.py`，可注入延迟拉大竞态窗口）和 `fake_worker_proc.py`（像 `run_worker` 一样写 `out/worker.json`、睡一会、写回执；`H_IGNORE_TERM` 时不理 SIGTERM）。只对测试自己拉起的车间进程组发 SIGTERM，测试结束收掉所有子进程。覆盖：

- 决策：11 种任务状态只有授权 + ready + 依赖完成 + 有车间负责方的入选，每条带原因码 / 证据 / 下一步；只读打开库、重复评估逐字相同、库文件字节不变；台账读不到全 hold；名额总 2 / 同项目 1（外来 running 单占名额，未开跑的 new 单不占）。
- 预留：同一任务第二个预留被拒、部分唯一索引兜底；4 个进程同时抢只 1 个成功；开单期间任务被撤销 → unsafe，只有 user 能收口；v1 / v2 库迁到 v3，只读打开 v2 库不迁移。
- 恢复：开单后 / 记单号后崩溃按关联键关联；没开出单要租约过期且预留方与开单子进程都确认退出才 void；多张单、半截目录、单已结束、记过单号却找不到 → unsafe；真实子进程开单前 / 开单后 SIGKILL 两例。
- 进程身份：match / gone / 僵尸 / PID 复用 / 别的主机 / 没记启动时间。
- 生命体征：心跳过期 3 天仍 alive；dispatch 与车间都被杀 → lost（不判失败）；dispatch 被杀车间迟到回执 → finished；无身份、PID 复用、外来进程、上一轮身份都不认。
- 取消：自有车间收 SIGTERM 停下、台账 failed、任务保持 canceled；与自然完成竞争不发信号；身份不明 0 信号；不理 SIGTERM 只报告不升级；真实派单根目录拒绝发信号、命令行只出建议。
- 重试分级、低噪声报告（未确认送达每轮再报、`ack` 后才安静、重启后去重仍在、没有待报时 `--quiet` 空输出）；`tick --execute` 默认关闭。
- 端到端：任务书 S7 场景 1、2、3、7、8（其余场景在上面各文件里），对照表在 `test_twin_scheduler_e2e.py` 文件头。

## 车间只读单（test_run_worker_readonly.py、claude_readonly_probe.py）

`test_run_worker_readonly.py` 钉住 `run_worker.py` 的只读参数：Claude 只读单只加载 Read / Grep / Glob（`--tools`）、不加载 MCP（`--strict-mcp-config`）、权限模式 `dontAsk`，续跑同样受限；写入单和 Codex（`-s read-only`）不变。来由：只加 `--allowedTools` 时，本机 `bypassPermissions` 下只读单照样能用 Bash、Write 和 MCP 写文件（2026-11-19 实测）。

`claude_readonly_probe.py` 是真 claude 的反向验证（会用会员额度、要联网，不进全量测试）：升级 Claude Code 或改了只读参数后手动跑 `python3 -B hermes/scripts/tests/claude_readonly_probe.py <证据目录>`。它用真实 `~/.claude` 设置、临时假仓库（项目设置故意放行 Write、Edit、`Bash(touch *)`），让模型真的去试 Write、Bash、MCP 写文件；工具只有 Read / Grep / Glob、没有 MCP、`dontAsk`、一个探测文件都没写出来才退出码 0。

## 派单锁与进程身份（test_dispatch_lock.py）

真实子进程跑 `dispatch.py`（`dispatch_harness.py`）。覆盖：4 个进程并行 `new` 编号互不相同；同仓库两张单同时 `run` 只跑一张、另一张排队；同一单同时 `run` 两次只跑一次；锁被占时等待有上限且不动作；`run` 记 `dispatch_pid / run_host / run_started`，车间 pid = pgid = sid；`write_identity` 与 `ps` 一致；`--sched-key` 写字段和标记文件、格式不对在编号前拒绝；手工 `new` 字段集合不变。改动前 9 项中 8 项失败。

## 调度返修（test_twin_scheduler_rework.py）

ChatGPT 2026-10-29 评审 F1 / F2 与返修终审 P0 的负向用例，18 项。F1 / F2 的 14 项先在返修前 `4154ef0` 上跑红（6 FAIL + 6 ERROR）。`dispatch_harness.py` 加了 `H_PRELOCK_DELAY`（拿派单锁前先睡，模拟锁前慢 `git fetch`）和 `H_DONE_FILE`（进程退出时写文件，给迟到的开单进程计时）。覆盖：

- F1：真实子进程在拉起迟到开单后、登记子进程身份前被 SIGKILL，租约过后预留判 unsafe 而不是 void，迟到的开单在锁内被拒、0 张单、任务不再可派；锁前慢 fetch 期间预留被作废，开单在锁内被拒；只有 claimed 的关联键能开单（void / released / unsafe / linked / 不存在 / 项目不符 / 状态库不存在都拒绝），手工开单照常；另一连接持写事务时开调度单不被阻塞（锁顺序）；游离单（已收口预留的单、未知关联键、半截目录）让任务 unsafe、报告报错误、`recover` 退出码 1，不删不关联；作废证明的三种情形。
- 终审 P0 同键只开一张（`DupKeyTests`，4 项，先在 `9709be7` 上两套 Python 各 4 FAIL）：同一个仍是 claimed 的键串行开两次，第二次拒绝并在 `existing` 列出首单、目录只有一张，关联后仍拒；三个进程用 `H_GO` 同时起跑、`H_DELAY` 拉长锁内窗口，恰好一个成功、两个拒绝；首单只写了 `sched_key` 标记没写 order.json、或只有 order.json 带同键，都拒绝；标记读不了、台账坏了也拒绝，拒绝时目录不增不减；不同键各自正常，既没标记也没台账的目录不算占用，手工开单照常。
- F2：命令行输出被丢弃后仍待报；`deliver()` 只有确认送达（True 或指纹列表）才 ack，失败 / None / 其他值 / 异常都保留；部分确认；重复 ack 幂等；`report --quiet` 没确认前重复输出、`ack` 后安静；报告不生成任务或工单。

## 影子合并官（test_merge_officer.py）与每周提案收口（test_proposal_digest.py）

假 gh、假 lark-cli 和临时目录，不联网、不碰真实记录和提案。影子合并官覆盖：风险档（L0 文档、规则文档上调 L1、按文件数分 L1/L2、敏感路径 L3 及按仓库的敏感路径）、七种退回理由、有退回理由时不靠「问常浩」绕过、判定不改入参；一致率按同一 HEAD 比对（新提交后旧判定标「取代」）、问常浩不计、回填单列不计入门槛、门槛边界（20 次 90% 达标，85% 或 19 次不达标）；回放前后对比且只读；命令行只调 `gh pr list` / `gh pr view`，一个仓库失败时报告并继续。提案收口覆盖：回复写法与八种错误（一条都不写）；三类来源的汇总（已定的不列、延期 / 待定照列、被取代的旧编号并入新条目、只读最新一期、LESSONS 默认不列，打开后只算名册项目近 7 天且未升级的）；发卡片的幂等键和同日不重发；写回格式（复盘条目标题下一行、decisions.md 追加行、LESSONS 台账）和改主意时替换结论行；给 Hermes 的草稿（含原文摘录，无参数入口 proposal_digest_draft.py）、卡片带推荐、推荐文件四种错误不发、「按推荐」及「按推荐，2 改否」覆盖、没有推荐时拒绝「按推荐」。再犯与待提升（#216）：PATH 最前面放假 `node`，回放 agent-kit `weekly-review.mjs --json` 的输出（`DT_WEEKLY_REVIEW` 指向临时目录里的替身脚本），不跑真的 agent-kit；覆盖只列「再犯」和〔提议〕两种（只写未升级的不列）、升级首选在前、每期 7 条截断并写「另有 N 条」、超出的记进期号状态并由 list 打印、DT_LESSONS_MIN_PROJECTS 为 0 时 LESSONS 候选仍不列（打开时两类共存、连续编号）、apply「按推荐」加逐条写进 guards-decisions.md 且不混进 LESSONS 台账、下期已定的不列延期的再列、汇总取不到（脚本出错、输出不是 JSON、schema 不对、脚本不在）时其他来源照常并写明原因、只有这句提示也照发且草稿不为空。已修未合并（复盘 N3）：`DT_GH` 指向临时目录里的假 gh，卡片末尾多一栏、不编号，草稿带 `unmerged`，只有这一栏有内容（查到条目或没查到）也照发，全查了且没有时不单独发，`list` 打印这一栏。

## 已修未合并（test_unmerged_fixes.py）

假 gh（临时目录里的脚本，按夹具回放 `pr list` / `pr diff`），不联网。判定：修复类标题（N1 的词加高危、审计；「修改」「修订」「fixture」不算）、正好 3 天不算超过、开得最久的在前；同一段改动按「一个 hunk 里连续的增删行」整段比较（照 11-19 trainee-hub #77～#80 的 CI 一行），交接文件、只有「}」的小块、删除行不同、中间隔了上下文的都不算，不比行号，非 ASCII 路径和删除的文件认得对。失败路径各一个测试，都不当成 0 条：找不到 gh、没登录（只调一次就停）、限流（之前查到的照列）、连不上、超时（第 1 次只算这个仓库，第 2 次起不再查）、一个仓库不在（其余照查，不当成网络问题）、输出不是 JSON 或缺字段、字段类型不对（createdAt 是 null、number 是字符串、缺 number）、diff 里有非 UTF-8 字节（换成替换符照比，不崩）、某个 PR 的 diff 太大（这个仓库 a 类照列）、开着的 PR 到上限、名册读不了。test_proposal_digest.py 另有一项：这一栏遇到没想到的异常，只写「本期没查到（不是 0 条）：意外错误」，整张周卡照发。

## 飞书一句话合并（test_merge_act.py）

真 git（临时裸仓库当 GitHub，带 `refs/pull/<号>/head`；临时主目录）、假 gh、假 lark-cli。覆盖：只给 act.repos 里的仓库发卡片、同一版本不重发、L3 卡片写明飞书里不合并；合并成功时看 CI、不在本机跑测试也不建 worktree、`gh pr merge` 带完整 `--match-head-commit`、记 owner、主目录拉齐；七种停下（版本变了、落后、冲突、不是 CLEAN、草稿、L3、本机主分支有没推的提交）和 CI 没过 / 在跑 / 没结果都不调 `gh pr merge`；没 CI 的仓库哪怕登记了测试命令也停下、PR 里的代码一行都不跑（#190 不变量 2）；服务端拒绝合并时如实报；只认发过的卡片；仓库没开时拒绝；主目录有未提交改动时不拉、不动文件；退回在 PR 留言并记 owner。

## Multica 夜跑主控（test_multica_night.py，#92）

62 项。假 multica（`fake_multica.py`）用一个 JSON 世界模拟 issue、run、消息、Wakeup、Autopilot，假 gh 回分支和 PR；每次调用主控都是新进程，计数只能从夜跑目录的 `state.json` 恢复。不联网、不碰真实 Multica、执行器容器和 `~/Library`。覆盖：建单（父单指派成员、子单 backlog、跨 stage 引用、幂等、每条命令都带沙盒工作区）、计划校验、开跑前核对（含名册 `sandbox: true` 门禁）、按 stage 推进与全部交付、run 还在跑时不推进、各停机条件（时长、run 总数、连续失败跨进程累计、blocked、停滞、run 不见了、消息序号缺口、家族外 issue 被改、未授权 Wakeup / Autopilot、脚本自身异常）、停机时只在父单留言且子单用 `--no-start`、waiting_local_directory 的 run 被取消、取消不掉就冻结、冻结后容器重启也不续跑、定时开跑（到点由 tick 开跑、过窗口不开、核对不过只说一次）、release、检查间隔异常、report 的 GitHub 越界核对；#93 独立审查后补：停机途中某步读不懂仍接着取消并冻结、家族外（含 chat）run 触发停机并被取消、人工 stop 撤销定时开跑、未 release 的夜跑不让 plan 挤掉、后面 stage 被提前开跑、推进中断后补推进、停机中断后接着停、父单被改指派时改回、结束后又有 run 只告警一次、tick 卡住超过 15 分钟告警、run 总数 19/20 边界。Wakeup 用真实返回体的 enabled 布尔值。ChatGPT High 第 1 轮后补：历史 run 消失而看得见的都已结束时仍冻结（P0-01）、身份字段漂移（P1-01）、主控卡住时应急冻结（P1-02）、冻结要独立回读确认否则每次重试（P1-03）、结束后 release 前新 run 一律取消（P1-04）、旧 stage 重入不推进（P1-05）、被取消的 run 写进 run-log（P1-06）、release 要绑定确认且冻结的不能非交互 release（P1-07）、人工 stop 崩了也冻结（P2-01）、子单被取消不放行（P2-02）、无效序号与崩溃后去重（P2-03）。第 2 轮后补：最后一次导出才发现缺口也冻结、回读之后冒出的 run 再取消再回读（取消不掉就冻结）、应急标记先于硬停落盘且单有标记也不推进、停机和冻结都失败时退出码非零、导出文件里已有序号不连续时查出缺口。
