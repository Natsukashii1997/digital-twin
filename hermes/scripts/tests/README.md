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

预期：全部测试均显示 `ok`（派单续跑 13 项 + 项目进展 7 项 + 派单接 GitHub 15 项 + 进展里的 PR 和 issue 2 项 + 每日巡检 6 项 + 仓库自检 5 项，共 48 项），末尾显示 `Ran 48 tests in ...s` 和 `OK`，退出码为 0。耗时随机器变化。`-B` 禁止生成字节码缓存。

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

## 仓库自检（test_check_repos.py）

测的是 `ops/check_repos.py`。每个项目一个临时裸仓库当远端、一个临时主目录，临时名册（`DT_ROSTER`）；假 `gh` 从状态目录的 `api.json` 回 `gh api` 的仓库设置、标签和文件内容，路径不在里面按 404；`fail_match` 让只含某个仓库名的调用失败。git 用临时全局配置。覆盖：全部合规时 `ok: true`、退出码 0，没有 `github` 的项目不查，跑完本地引用不变（不 fetch）；远端不私有、默认分支不对、rebase 没关、不自动删分支、Issues 没开、缺标签、没有 PR 模板、入口文档没有「工程流程」逐项记失败，名册 `test` 为空只记提示；主目录不在主分支记失败且不切分支；本地主分支多出没推的提交记失败，远端有本地没有的提交记「本地落后，需拉取」提示；某个项目 gh 连不上只把该项目标失败、不再逐项调 gh，其他项目照查。
