# 贡献指南（newscrawl）

本仓库是个人新闻采集项目，PR 只进本仓库，用于自我评审与留痕。

## 分支与提交

- 从 `main` 切功能分支：`feat/<主题>`、`fix/<主题>`、`docs/<主题>`、`chore/<主题>`（小写、短横线分隔）。
- 提交信息用 conventional commit：`feat:` / `fix:` / `docs:` / `test:` / `refactor:` / `chore:`，正文写清原因与影响面。
- 一个 PR 一件事；`gov_news/` 的改动不要夹带无关目录。
- 不直接推 `main`；不推上游远端 `origin`（那是 Scrapling 的只读镜像）。

## PR

- 按 `.github/PULL_REQUEST_TEMPLATE.md` 填写。
- 「验证结果」必须是**实际跑过的命令与读数**；没跑就写没跑、为什么没跑。
- 本机没有 `gh` CLI：推完分支后在浏览器开
  `https://github.com/impeccable992/newscrawl/compare/main...<分支>?expand=1` 创建 PR。

## 环境

- 运行统一用 `uv run --isolated --extra ai ...`；不要改仓库自带 `.venv`。
- 国内网络设 `UV_DEFAULT_INDEX=https://pypi.tuna.tsinghua.edu.cn/simple`。
- 代理 `http://127.0.0.1:7897` 是本机环境配置，脚本只提供参数，不写死进代码。
- 凭据（cookie、token、登录态）不进仓库。

## 已知环境问题

SSH 到 GitHub 会间歇性 `Connection reset`：`fetch`/`push`/`ls-remote` 失败先重试 2~4 次再判断是否真失败。

## AI 协助

使用 AI 协助的改动，按上游 `AI_POLICY.md` 在 PR 里披露并说明协助程度。
