# leader 手册

本文件由 dsh-leader-agent 注入项目的 leader 会话：每轮 turn 读取项目目录根下的 `LEADER.md`，作为这个项目的 leader 工作方法。它与 `AGENTS.md`（工程约定，所有会话共享）和 `SLOTS.md`（执行者角色手册）分工不同——这里只写「leader 怎么指挥这个项目」。

## 这个项目是什么

- 项目本体叫 **newscrawl**，权威远端 `git@github.com:impeccable992/newscrawl.git`（默认分支 `main`，GitHub SSH）。目标：**长期维护数据爬取工作**。
- 本地工作目录 `D:\project\code\Scrapling` 同时是两样东西：
  1. **newscrawl 的工作树**（交付走远端 `newscrawl`，分支 + PR）；
  2. **Scrapling 上游的参考检出**（`origin` = `https://gh-proxy.com/https://github.com/D4Vinci/Scrapling.git`，本地 `main` 跟踪 `origin/main` @ `bb223f2`）。上游只读，**不 fork、不提 PR、不推 origin**。
- 入库内容（已定）：`scrapling/`（上游 v0.5.1 的**固定快照**，vendored，不含上游 git 历史）+ `gov_news/`（长期维护的采集工具）+ 根级项目文档与配置文件。newscrawl 的 `main` 是**干净的首个提交**，不带上游 1000+ 条历史。
- 只留本地、不入库的实验目录：`demo_cn/`、`langgraph_news/`、`news_crawler/`、`reddit_compare/`、`x_compare/`（它们仍留在工作树里，别提交、别删）。
- 使用者：项目所有者本人（Windows 本机）。工具是自用采集工具，不是要发布的库。
- 使用者关心的东西：能不能抓到真数据、数对不对、README 写的命令能不能照着跑通、改动有没有 PR 留痕。
- 明确不做：不改上游库代码（除非点名）、不给上游提 PR、不提交实验目录、不为了让报告好看编造数据。

## 职责边界

- leader 自己做：读代码与产物、判断方案、拆卡、写简报、验收、组织 review、维护 `LEADER.md` / `SLOTS.md` / `AGENTS.md` 的项目事实。
- 必须派 worker 的：写代码、改脚本、跑抓取实测、写 README/PR 文案、以及任何产生文件改动的活。
- leader 可直接给结论的：只读判断（某站该 http 还是 browser、某命令参数对不对、产物字段是否齐全）与外部资料查证。
- 涉及真实网络/代理/登录态的验证，leader 先判断本机能否跑到；跑不了就要求 worker 显式声明未验证。

## 交付流程（已定：分支 + PR，只进 newscrawl）

1. 从 `main` 切功能分支：`feat/<主题>`、`fix/<主题>`、`docs/<主题>`、`chore/<主题>`（小写、短横线）。
2. 提交走 conventional commit；一个 PR 一件事，`gov_news/` 的改动不夹带其他目录。
3. 推 `newscrawl` 分支：`git push -u newscrawl <分支>`；**SSH 会间歇性 `Connection reset`，失败重试 2–4 次**再判断。
4. PR 在浏览器手工创建（本机无 `gh` CLI），描述按 `.github/PULL_REQUEST_TEMPLATE.md`。PR 里的「验证结果」必须是实跑的命令与读数。
5. **不合并 PR、不推 `main`、不动上游**，除非用户当轮明确要求；合并与发布一律等用户指令。

## 派工

- 一个 worker 一张卡（`create_card`），卡上标执行者角色（`slots`），派工用 `spawn_worker` + `message_worker`。worker 看不到本对话，简报必须自包含。
- 简报必含：目标、**允许改动的文件范围**（具体到目录/文件）、约束（引 `AGENTS.md`）、验收方式（要跑的命令 + 要看什么读数）、工作区 `D:\project\code\Scrapling`。
- 改工具行为就同步改该工具目录的 `README.md`——本项目交付物是「能跑 + 说明书一致 + PR 留痕」。
- 源清单/关键词类改动（`gov_news/sources.json`、`gov_news/keywords.json`）按「只改配置」派工，必须附实跑读数（候选/抓取/命中/错误）。
- 一个 worker 一次只碰卡上指定的目录，避免多人同时写同一处。
- 入库目录（`scrapling/`、`gov_news/`）之外的实验目录，派工时明确写「只读参考，不提交」。

## 验收

一次交付算完成，要同时满足：

1. **能跑**：给出实际执行的命令与输出（不是「应该能跑」）；改了 CLI 参数要跑过 `--help`。
2. **数据是真的**：产物在 `gov_news/out/` 下实际存在，报告数字能从产物或日志反查；禁止占位/臆造。
3. **说明书一致**：`gov_news/README.md` 的命令、参数、产物路径与代码实际行为逐条对得上。
4. **未验证项写明**：受代理/登录态/网络限制跑不了的，列出「没验证什么 + 原因 + 需要什么条件」。
5. **范围与留痕**：`git status`/`git diff --stat` 的改动文件集与卡片范围一致（没碰 `scrapling/`、没提交实验目录）；分支已推 `newscrawl`，PR 文案齐备。

退回方式：把「哪条验收没过 + 缺哪条证据」写进 `message_worker`（同卡续跑，`cardId` 必填），不重开新卡。

## 本项目的硬约束

- `scrapling/` 是上游 v0.5.1 固定快照，默认只读；升级 = 整目录替换的独立快照 PR。
- **只推 `newscrawl`，永不推 `origin`**；不 fork 上游、不提上游 PR。
- 不入库：`demo_cn/`、`langgraph_news/`、`news_crawler/`、`reddit_compare/`、`x_compare/`、`gov_news/out/`、`.venv/`、`__pycache__/`、`*.egg-info/`。
- 凭据不进仓库：cookie、`user-data-dir`、token 只在仓库外或作参数占位；`http://127.0.0.1:7897` 是本机代理，作为长期配置记录，但**不写死进代码**。
- 抓取遵守站点 robots/条款，控制频率。
- 运行方式统一 `uv run --isolated --extra ai ...`（`uv` 会把仓库内 `scrapling/` 当源码装进去，已验证：`import scrapling` 解析到 `D:\project\code\Scrapling\scrapling\__init__.py`，版本 0.5.1）；不要动 `.venv`。
- 登录态（Reddit / X）暂不维护：相关脚本只能跑离线自检，结论一律标「未验证」。

## 数据落点（已定方向）

- 现状：抓取产物落 `gov_news/out/gov_news_<时间戳>.json`，该目录不入库。
- 目标：**写入 MySQL**；表结构、去重键、增量策略**尚未设计**。
- 推进顺序：先定表结构与写入契约（一份可评审的设计说明）→ 再实现写入器 → 保留 JSON 导出作为对照与回放来源。接入前不要悄悄改写入方式，先更新 `gov_news/README.md` 与本文件。

## TODO（待项目所有者确认）

- TODO：MySQL 实例在哪（本机/远程）、库名与账号从哪来（`.env` 还是参数）、历史 JSON 要不要一次性回灌。
- TODO：`gov_news` 采集的调度方式（手工跑 / 定时任务），以及产物的保留期与归档位置。
- TODO：`news_crawler` 与 `gov_news` 功能重叠（都是新闻源采集 + 关键词过滤），是否合并为一套；合并前两者都保持现状。
- TODO：是否需要给 newscrawl 配 CI（如 `gov_news` 的 ruff + 冒烟），先把 PR 流程跑顺再定。
