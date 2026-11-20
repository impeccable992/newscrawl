# newscrawl

输入关键词进行新闻获取。

## 这是什么

`gov_news/` 是按关键词批量采集新闻的爬虫工具；`scrapling/` 是上游
[Scrapling](https://github.com/D4Vinci/Scrapling) **v0.5.1 的固定快照**（vendored，
随仓库一起版本化，便于本地跟读与调试）。

## 怎么跑

```powershell
# 在仓库根目录执行；uv 会把仓库内的 scrapling/ 当源码装进去
$env:UV_DEFAULT_INDEX='https://pypi.tuna.tsinghua.edu.cn/simple'
uv run --isolated --extra ai python gov_news/crawl.py --limit 3
```

完整参数与源清单说明见 `gov_news/README.md`。产物落 `gov_news/out/gov_news_<时间戳>.json`，
该目录不入库；后续计划改为写入 MySQL。

## 目录

| 路径 | 说明 |
|---|---|
| `scrapling/` | 上游 v0.5.1 快照，只读；升级 = 整目录替换的快照提交 |
| `gov_news/` | 采集工具：`crawl.py`、`sources.json`、`keywords.json`、`README.md` |
| `AGENTS.md` | 工程约定（所有会话共享） |
| `LEADER.md` | 任务拆分与验收口径 |
| `SLOTS.md` | 执行者角色手册 |
| `CONTRIBUTING.md` | 提交与 PR 流程 |

## 分支与 PR

改动走功能分支 `feat|fix|docs|chore/<主题>` + PR，不直接推 `main`；PR 只进本仓库，用于自我评审。
