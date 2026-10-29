# hermes/scripts 自测

名册里 digital-twin 的 test 就是这条命令，合并前会自动跑：`python3 -B -m unittest discover -s hermes/scripts/tests`。

## 派单续跑回执（test_dispatch_followup.py）

只需要 Python 3.8 或以上版本的标准库，无需安装依赖。在仓库根目录执行：

```sh
python3 -B -m unittest discover -s hermes/scripts/tests -v
```

也可以直接运行脚本：

```sh
python3 -B hermes/scripts/tests/test_dispatch_followup.py
```

预期：全部测试均显示 `ok`（派单续跑 13 项 + 项目进展 7 项 + 派单接 GitHub 15 项 + 进展里的 PR 和 issue 2 项 + 每日巡检 11 项 + 工程自检 21 项 + 仓库自检 6 项 + 日报周报 13 项 + 持久状态底座 17 项，共 105 项），末尾显示 `Ran 105 tests in ...s` 和 `OK`，退出码为 0。耗时随机器变化。`-B` 禁止生成字节码缓存。

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

- 漂移：全部一致时无输出；安装缺文件、内容不同只报路径不报内容；`check_repos.py` 两处安装都查；`hermes/scripts/tests/`、非 `.py` 脚本、安装处多出的文件不管；工作区未提交改动和别的分支的提交不算漂移；仓库不在或不是 git 仓库记一条「取不到仓库 main」。
- 日期：非 1 号只查漂移；1 号或 `--monthly` 才查沙盒和仓库；端到端用空名册跑真的 `check_repos.py`，名册读不了报失败项。
- 沙盒（从巡检迁来）：只算顶层、非隐藏、超过 30 天没动的；目录扫不了记一条「扫不了」，不崩。文案和「另有 N 项」截断不变。
- 仓库自检（从巡检迁来，打桩 `subprocess.run`）：只返回失败项，全通过或只有提示返回空；用当前 Python 和同目录 `check_repos.py`、继承 `DT_ROSTER`、600 秒超时；空输出、损坏 / 不一致的 JSON、意外退出码、启动失败和超时都记「仓库自检未完成」，不带出 traceback。

不联网，不碰真实仓库、`~/.hermes`、安装副本和 Vault。单跑：`python3 -B -m unittest discover -s hermes/scripts/tests -p test_self_check.py -v`。

## 仓库自检（test_check_repos.py）

测的是 `ops/check_repos.py`。每个项目一个临时裸仓库当远端、一个临时主目录，临时名册（`DT_ROSTER`）；假 `gh` 从状态目录的 `api.json` 回 `gh api` 的仓库设置、标签和文件内容，路径不在里面按 404；`fail_match` 让只含某个仓库名的调用失败。git 用临时全局配置。覆盖：全部合规时 `ok: true`、退出码 0，没有 `github` 的项目不查，跑完本地引用不变（不 fetch）；远端不私有、默认分支不对、rebase 没关、不自动删分支、Issues 没开、缺标签、没有 PR 模板、入口文档没有「工程流程」逐项记失败，名册 `test` 为空只记提示；主目录不在主分支记失败且不切分支；本地主分支多出没推的提交记失败，远端有本地没有的提交记「本地落后，需拉取」提示；某个项目 gh 连不上只把该项目标失败、不再逐项调 gh，其他项目照查。

## 日报周报取数和写回（test_report.py）

PATH 不动，用 `DT_LARK_CLI` 指向假 `lark-cli`（`fake_lark.py`，不联网，按临时目录里的 state.json 回话，`docs +update` 会真的改假文档以便回读）；项目进展、台账种子、证据目录都换成临时路径。覆盖：私聊全收、和猛鬼兽的私聊不收、自己给自己的备忘收、群只收常浩发言或被 @ 的；飞书任务只收常浩负责、近期到期或当天新建的；前一篇台账优先于种子；同一天已写过、周末没动静输出 SKIP；lark-cli 失败退出码 1；周报只取本周日报、按日期排；写回插在说明段之后、同一标题不重写、星期 / 缺段 / 表格 / 标题格式 / 周号不对时拒绝且不调用写入。

## 持久状态底座（test_twin_state.py）

测的是 `twin_state.py`（B1：Goal / Task / Run / Event / Ref）。每项测试用独立临时目录里的状态库（`Store(path, clock=…)`，固定时钟 2000-01-01 09:00+08:00）和虚构的派单台账目录；不碰 `~/.hermes`、真实工单、安装副本，不调用模型或外部服务。覆盖：

- 任务书 B1.5 十项验收，按编号 `test_01` ～ `test_10`：建目标与任务；未授权不能进 ready / 不能开跑、Hermes 不能授权、目标撤销连带任务；一个任务多次 Run（失败 → 重试 → 成功，再挂一张既有工单）；Run 成功只到 review，merged / cleaned 不动任务，目标完成只有常浩能定；同一幂等键的开跑 / 回执 / 通用事件只处理一次（`dup_count` 计数），终态后的不同回执被拒；关库重开状态不变，过期 Run 标 stale、任务转技术阻塞，依赖完成自动放行，running 却没 Run 的任务转阻塞，handler 中途抛异常整体回滚、同键可重来；失败 / 超时 / 护栏 / need_decision 各自的阻塞类型和原因；decision 类阻塞对 hermes / claude / codex / system 的重试与解阻塞一律拒绝且留痕，technical 重试有上限；外部探针抛异常或返回空只记 `ref.unreachable`、任务不动，核对到也不自动完成，没证据不能完成；台账同步只建未关联的 Run、不建目标和任务，坏文件跳过、重同步无变化、关联后台账跑完才反映到任务、台账跳状态也跟得上、旧台账文件原样。
- 其余：非法转换与绕过状态机改状态；撤销规则；返工、心跳、引用；备份 / 恢复 / 库版本护栏；库文件 600、目录 700；命令行往返（init、goal-new、task-new、task-ready、run-start 带幂等键、run-receipt、ref-add、task-complete、show、list、events、goal-complete、recover、backup、restore --yes）以及拒绝时 `ok: false`、退出码 1。

系统 `/usr/bin/python3`（3.9.6）和 `python3`（3.14）都要能通过：模块只用 3.9 有的语法和标准库。单跑：`python3 -B -m unittest discover -s hermes/scripts/tests -p test_twin_state.py -v`。
