<!-- dsh-leader-agent:conventions:start -->
# 仓库工程约定

本节由 dsh-leader-agent 写入，作为本仓库的工程约束。它是骨架不是全部——项目特有的规则（架构不变式、目录拓扑、发布流程）在项目自己的文档里定义。

## 沟通与表达

- 注释和日志优先中文，保留专业术语；面向用户的文案遵循项目既有语言约定（本仓库上游正文、README、文档默认英文；`docs/README_CN.md`、`docs/locales/zh/**` 为中文版）。
- 报告结论先行，给必要的证据与限制；不写「看起来正确」这类无法核验的判断，缺证据就说缺证据。
- 事实、推断与未验证项分开陈述；未知写未知，不用印象代替核验。
- 完整表达必要的 tradeoff 与风险，不用简洁压掉失败与阻塞。

## 注释纪律

- 默认不写注释，只在 WHY 不明显时补一句。
- 不解释代码做了什么，不引用任务背景或变更历史。
- 注释与被注释代码同生共死；删除代码时一并删除失效注释。
- 对齐文件既有注释密度与措辞风格。

## 代码风格

- 目标运行时是 Python 3.10+（`pyproject.toml` `requires-python = ">=3.10"`，`ruff.toml` / `[tool.mypy]` / `[tool.pyright]` 都锚定 py310）：不用 3.11+ 专有语法；`vermin -t=3.10- --violations` 在 pre-commit 里守这条线。
- 类型注解按上游要求写全：本仓 README 声明「Full type hints」，`mypy`（`check_untyped_defs = true`）与 `pyright`（basic）在 CI 强制；新增公开 API 必须带注解。
- 类型导入与内置类型模块统一走 `scrapling.core._types`（`ruff.toml` 的 `typing-modules` 已声明）。
- 不用 `Any` 兜底类型问题；能用具体类型或联合类型就别退化成 `Any`。
- `try` 只包可能抛出的那一条语句，避免 `catch` 波及无关失败。
- 行长 120（`ruff.toml`），字符串用双引号，lint 规则集 `E/F/W`。
- 标记约定：`FIXME`（阻断发布）、`TODO`（尽快处理）、`XXX`（低优先级优化）。
- 所有工具调用走原生 tool call 形式，不用文本标记模拟协议。

## 失败处理

- 配置错误与未知异常大声报错（Fail Loud），严禁静默吞错。
- 空 `catch` 必须在单行注释中写明吞没原因。
- 输入校验放在系统边界（用户输入、外部 API），内部假设不做重复防御。

## 工程判断

- 新需求先检验前提，再选实施；选解决根因的最小完整改动，不做投机泛化。
- 不为一次性操作创建抽象，不为不可能发生的场景加防御。
- 改动前先搜索既有实现，复用已有模式，最小化变更范围。
- 不做范围外的事：不顺手改无关代码，不新建不必要的文件。
- `scrapling/` 是上游 **v0.5.1 的固定快照**（vendored，不含上游 git 历史），默认只读；改它必须由用户明确点名，并在同一批改动里说明「与上游的差异」。升级到新版本 = 整目录替换的快照提交，不在仓库里保留补丁层。
- 结构取舍有多种合理解法时，先写清楚真实用法与所有权归属，再决定形态。

## 状态与数据

- 状态管理遵循宿主与项目既有方案，不另建第二套状态源。
- 单一写入者原则：共享写入优先消除，确实必须共享时明确串行机制。
- 凭据（cookie、token、登录态）一律不进仓库：`--user-data-dir`、cookie 文件、`.env` 放仓库外，脚本里只留参数占位。
- 抓取产物落各自工具的 `out/` 目录，文件名带时间戳，便于复跑对比。

## 用户可见文本

- 上游文档是多语的（`docs/locales/**` 十种语言 + `zensical.*.yml`）：改上游文档结构时，同一批改动内同步各语言版本，缺失译文在交付前补上。
- 自己新增的工具文档（各工具目录下的 `README.md`）用中文写，命令与产物路径必须与代码实际行为一致。

## 本地工具链

- 统一用 `uv`（本机 0.12.1），脚本按既有写法 `uv run --isolated --extra ai ...` 运行。
- 仓库自带的 `.venv`（Python 3.14.6）是 editable 安装、**没有 pip 也没有 fetchers/ai 额外依赖**，直接用它跑脚本会缺 `curl_cffi` / `patchright` / `markdownify` / `mcp`；不要往 `.venv` 里手动塞包。
- 国内网络保留 `UV_DEFAULT_INDEX=https://pypi.tuna.tsinghua.edu.cn/simple`（既有脚本已默认设置）。
- 两个远端别搞混：`origin` = `https://gh-proxy.com/https://github.com/D4Vinci/Scrapling.git`（上游只读镜像，本地 `main` 跟踪 `origin/main` @ `bb223f2`）；`newscrawl` = `git@github.com:impeccable992/newscrawl.git`（本项目唯一权威远端，走 GitHub SSH）。
- **只往 `newscrawl` 推；永远不推 `origin`**，也不给上游提 PR（上游是别人的项目、本地不 fork）。
- 代理：长期配置为 `http://127.0.0.1:7897`。访问被墙/需要代理的站点（Reddit、X 等）必须带上它；它是本机环境配置，**不要写死进代码**，脚本只提供参数（示例如 `--proxy`）与配置默认值。
- 抓取产物：当前落 `gov_news/out/`（JSON，带时间戳），该目录不入库。**目标形态是入 MySQL**，库表设计未定；接入前先更新 `gov_news/README.md` 与 `LEADER.md`，不要悄悄改写入方式。
- 登录态（Reddit / X）暂不维护：`reddit_compare`、`x_compare` 在拿到登录态之前只能跑 `--selftest` 一类离线检查，相关结论一律标「未验证」。

## 远端与 PR 流程（newscrawl）

- 权威远端 `newscrawl`（`git@github.com:impeccable992/newscrawl.git`），默认分支 `main`；一切改动走分支 + PR，不直接推 `main`。
- 分支命名：`feat/<主题>`、`fix/<主题>`、`docs/<主题>`、`chore/<主题>`，全小写、短横线分隔，如 `feat/gov-news-mysql-writer`。
- PR 只进自己的仓库，用于自我评审与留痕，不对外提交。
- 一个 PR 一件事：改动目录写清楚；`gov_news/` 的改动不要夹带其他实验目录的内容。
- PR 描述按 `.github/PULL_REQUEST_TEMPLATE.md` 填写，**验证结果必须是实际跑过的命令与读数**；没跑就说没跑。
- 本机没有 `gh` CLI：PR 在浏览器手工创建（推完分支后开 compare 链接即可）。
- **SSH 到 GitHub 会间歇性 `Connection reset by port 22`**：`ls-remote` / `fetch` / `push` 失败先重试（2–4 次、每次间隔几秒）再判断是不是真失败，别一次失败就改走 HTTPS。
- 本机 git 身份只在仓库内配置（`user.name` / `user.email`）；换机器或重装后先确认身份再提交。
- 上游远端 `origin` 只作只读参考，更新快照是「拉上游 → 整目录替换 `scrapling/`」的动作，单独开 PR。

## 测试与验证

- 上游测试用 pytest，分三组跑（`tox.ini` / `pytest.ini` 已定）：并行组 `pytest tests/ -m "not asyncio and not browser" -n auto`；浏览器组 `pytest tests/ -m "browser"`（顺序跑，避免浏览器冲突）；异步组 `pytest tests/ -m "asyncio and not browser"`（顺序跑）。
- 前提：这三组只在本地工作树（有 `tests/`）成立。`newscrawl` 仓库**只含 `scrapling/` 快照**、不含上游 `tests/` 与 `docs/`，clone 出来跑不了；仓库内对采集工具的验收走各工具 README 里的实跑命令（`gov_news/README.md` 为准）。
- `pytest.ini` 默认带 `--doctest-modules`：改 docstring 里的示例等于改测试。
- 本地只跑覆盖改动面的最小证据链，不盲目跑全量套件。
- 测试里的网络依赖用 `pytest-httpbin` / `werkzeug`（`tests/requirements.txt`）提供的本地替身，不打真实外网站点。
- 冒烟/演示脚本的产物目录（如 `demo_cn/out/dev_cache`）会跨运行保留，复现对比前先清理。
- 重大缺陷修复必须固化永久回归用例，并在同批改动中记录原因。
- 测试失败就说失败并附输出；没验证就说没验证，不制造全绿假象。

## 提交与交付

- 提交信息用 conventional commit 前缀（`feat:` / `fix:` / `docs:` / `test:` / `refactor:` / `chore:`），细节写进正文（`CONTRIBUTING.md` 的口径），不要把作者名字写进代码。
- 用 AI 协助的改动必须按 `AI_POLICY.md` 在 PR / issue 里披露，并说明协助程度。
- 向上游提 PR 一律对 `dev` 分支，main 分支的 PR 会被拒。**本项目不适用**：本地不 fork 上游、不推 `origin`，所有 PR 只进 `newscrawl`。
- 提交前先修复再暂存最后提交；保持每个提交自洽，不把无关改动混进来。
- 推送前确认变更文件集与影响面，按改动范围选择最小验证证据。
- 独立变更拆开提交；修复引入的提交先于其传播。
- 改写历史一律 `--force-with-lease`，禁止裸 force-push。
- 不跳过 hooks、不绕过签名；hook 失败时排查并修复根因（`pre-commit` 跑 ruff / bandit / vermin）。
- 交付边界是「实现 + 验证 + 提交 + 发起评审」；合并与发布需要用户明确指令。
<!-- dsh-leader-agent:conventions:end -->
