# 官网/涉海机构新闻采集（站内检索优先 + 入库）

按 `sources.json` 的源清单采集官网/涉海机构新闻：**先走站点自己的检索（站内检索优先），检索不可用/
失败/0 条时自动回退该源的列表页**，进详情页取标题+正文，按 `keywords.json` 过滤，再写进 MySQL（每源一表）。
控制台给每源的**检索方式 + 状态 + 四个计数**，末尾一张汇总表；有任一源失败时进程退出码非 0。

```bash
# 全部启用的源，每源抓 2 篇详情，写库（参数默认值来自 gov_news/settings.json）
uv run --isolated --extra ai --with pymysql python gov_news/crawl.py --fetch-limit 2

# 只跑纯静态检索的源，不写库（没有 db.py 也能完整验证检索/报告）
uv run --isolated --extra ai python gov_news/crawl.py --sources mod,mod_qwfb,mil81,people --fetch-limit 2 --no-db

# 无站内检索的源：报告会标「无站内检索→回退列表页」，并按 settings.list_pages 翻列表页
uv run --isolated --extra ai python gov_news/crawl.py --sources ccg,nanhai --fetch-limit 2 --no-db

# scspi 只有加代理才通（不加会失败并回退，退出码非 0）；也可把地址写进 settings.json 的 proxy
uv run --isolated --extra ai python gov_news/crawl.py --sources scspi --fetch-limit 2 --no-db --proxy http://127.0.0.1:7897

# 临时换检索词（覆盖 keywords.json 的 global）
uv run --isolated --extra ai python gov_news/crawl.py --keyword 南海,黄岩岛 --fetch-limit 3 --no-db

# 只取候选、不抓详情不入库（最快）= 收集候选 + 打印前 fetch-limit 条
uv run --isolated --extra ai python gov_news/crawl.py --sources mod --fetch-limit 10 --list-only

# 把收集上限临时提到 1000（不传 --search-limit 时用它 settings.search_limit，默认 300）
uv run --isolated --extra ai python gov_news/crawl.py --sources mod,mil81 --list-only --search-limit 1000

# 要"不设上限"（翻完为止）：**只能**把 settings.json 的 search_limit 改成 0/负数再跑，
# 命令行故意不提供这个开关（仍受 5000 条护栏约束）：
#   { "search_limit": 0, ... }  ->  uv run ... --sources mod,mil81 --list-only

# 换一套参数文件跑（文件不存在不报错，全部走源配置/内置默认）
uv run --isolated --extra ai python gov_news/crawl.py --settings gov_news/settings.ci.json --sources mod --list-only

# 显式导出本轮结果（默认不导出；导出的文件可直接被 --from-json 回灌）
uv run --isolated --extra ai python gov_news/crawl.py --sources people --fetch-limit 3 --no-db --dump-records gov_news/out/people_dump.json

# 把已有 JSON 回灌入库（这条路径只入库，不抓取）
uv run --isolated --extra ai --with pymysql python gov_news/crawl.py --from-json gov_news/out/gov_news_20261117_161824.json
```

## 命令行参数（与 `--help` 逐条对应）

| 参数 | 说明 |
|---|---|
| `--config` | 源清单路径，默认 `gov_news/sources.json` |
| `--keywords` | 关键词表路径，默认 `gov_news/keywords.json` |
| `--settings` | 每轮运行的全局参数文件，默认 `gov_news/settings.json`；**文件不存在不报错**，全部走源配置/内置默认 |
| `--sources` | 只跑指定源（key 逗号分隔），默认全部启用的源 |
| `--fetch-limit` | 每源最多抓几篇**详情**（0=用 settings 的 `fetch_limit`，源配置 `limit` 兜底）。**不截断候选收集** |
| `--limit` | `--fetch-limit` 的**向后兼容别名**，与它完全等价（旧命令不用改） |
| `--search-limit` | 每源**检索阶段最多收集多少候选**（0=用 settings 的 `search_limit`；settings 里 0/负数 = 不设上限，仍受 5000 条护栏约束） |
| `--list-pages` | 回退列表页最多翻几页（0=用源配置 `list_pages`，源没写才用 settings 的 `list_pages`，上限 50） |
| `--keyword` | 临时覆盖本次的**检索词**，同时替换 `keywords.json` 的 `global` **过滤词**（逗号分隔）；`per_source` 不受影响，回退路径仍会追加 |
| `--no-filter` | 回退列表页时不做关键词过滤，全部保留（检索路径本来就不本地筛）；日志里这类条目标「保留（--no-filter 未做过滤）」而不是「丢弃」 |
| `--list-only` | 只取候选列表 + 打印前 `--fetch-limit` 条，**不抓详情页、不入库**（不写库这条行为有回归测试守着） |
| `--search-pages` | 每个检索词翻几页（0=用源配置，上限 20） |
| `--delay` | 请求间隔秒数（必须 ≥1；0=用 settings 的 `delay_seconds`，内置 1.0） |
| `--proxy` | 代理地址，**只对配置里 `search.proxy=true` 的源生效**（目前只有 scspi）；传空则用 settings 的 `proxy` |
| `--no-db` | 不写库（只做检索/报告，便于离线验证） |
| `--dump-records` | 把本轮结果导出到指定 JSON 路径（默认不导出） |
| `--from-json` | 不抓取，直接把指定 JSON 回灌入库 |
| `--db-name` | MySQL 库名（默认用 settings 的 `db.name`，其次 `newscrawl`） |
| `--db-password` | MySQL 密码（默认取 `GOV_NEWS_DB_PASSWORD` 环境变量，其次 `db.py` 的默认值；**不写进 settings.json**） |
| `--timeout` | 单个请求超时秒数（0=用 settings 的 `timeout_seconds`，内置 30） |
| `--verbose` | 打印每个请求的 INFO 日志（默认只留报告） |

## `settings.json`：每轮怎么跑

优先级：**命令行 > `settings.json` > 源配置(`sources.json`) > 代码内置默认**。

| 字段 | 默认 | 作用 |
|---|---|---|
| `search_pages` | 5 | 每个检索词翻几页（上限 20）；**源配置 `search.pages` 优先于它**（见下方优先级说明） |
| `search_limit` | 300 | 每源**收集候选**上限；**0 或负数 = 不设上限**（仍受 5000 条护栏约束） |
| `fetch_limit` | 50 | 每源**抓详情**篇数 |
| `list_pages` | 3 | 回退列表页翻几页；源配置 `list_pages` 优先于它 |
| `delay_seconds` | 1.0 | 请求间隔（必须 ≥1） |
| `timeout_seconds` | 30 | 单个请求超时 |
| `proxy` | `""` | 代理地址；**只对 `search.proxy=true` 的源生效**，国内源永不带 |
| `db` | `{name,host,port,user}` | 入库连接参数；**这里不放密码**，密码走 `--db-password` / `GOV_NEWS_DB_PASSWORD` |
| `source_overrides` | `{}` | 单源覆盖，如 `{"xinhua": {"fetch_limit": 20}}`（能压过源配置的 `limit`） |

**优先级不是一条链，分两种口径**（都在 `crawl.py` 的 `resolve_*` 里）：

| 参数 | 优先级 |
|---|---|
| `search_limit` / `fetch_limit` / `list_pages` / `delay` / `timeout` / `proxy` / `db.*` | 命令行 > **settings** > 源配置 > 内置默认 |
| `search_pages` | 命令行 > **源配置 `search.pages`** > settings > 内置默认 |

`search_pages` 之所以把源配置放前面：`xinhua_mil` 这种源刻意把 `pages` 压到 1 来压住浏览器开销
（见 `sources.json` 的 note），一个全局 `search_pages` 不该把它顶掉；settings 只给没写 `pages` 的源兜底。

```json
{
  "search_pages": 5, "search_limit": 300, "fetch_limit": 50, "list_pages": 3,
  "delay_seconds": 1.0, "timeout_seconds": 30, "proxy": "",
  "db": { "name": "newscrawl", "host": "127.0.0.1", "port": 3306, "user": "root" },
  "source_overrides": { "xinhua": { "fetch_limit": 20 }, "cctv": { "fetch_limit": 10 } }
}
```

### `fetch_limit` 怎么定：单轮耗时的三档实测（2026-11-20 同一台机）

| 源类型 | 单篇详情成本（实测） | `fetch_limit=50` 的单源估计 |
|---|---|---|
| 静态 HTML（mod / mil81 / people / ccg_wqzf 等） | 搜 5 篇 14.7s → 20 篇 29.6s，增量约 **1.0s/篇**（第 1 篇含建连约 4s） | **约 60–90s/源** |
| `browser` 型（xinhua / xinhua_mil / xinhua_world） | 取 3 篇 11.5s → 10 篇 74.8s，增量约 **7.1s/篇**（检索页导航 5–7s 另计，见下节） | **约 350–400s/源**，不可接受 → `source_overrides` 压到 20（≈150s） |
| `results_page`（scspi，需代理） | 同静态量级（结果页 1 次请求 + 详情静态） | 约 60–90s/源（不含代理往返） |

因此**默认 `fetch_limit` 取 50 只对静态源安全**。参考量级：4 个 api 源的检索阶段实测 135.7s
（`--search-pages 20 --search-limit 0`），若每源再抓 50 篇详情 → **约 7–8 分钟一轮**；
加上 xinhua 系（各压到 20 篇）+ 其余回退源，**全量一轮约 20–25 分钟**。
要更快就把 `fetch_limit` 调到 10–20（静态源约 20–40s/源）；把省下的时间花在
`search_limit`/`search_pages` 上更划算——收集候选只花检索请求的钱，不会触发浏览器详情页。

## 三个上限别再搞混（`search_limit` / `fetch_limit` / `list_pages`）

一轮每个源固定按这个顺序走：**先按 `search_limit` 把命中全部收回来并去重 → 再按
`fetch_limit` 截取要抓详情的那批 → 入库**；回退列表页时"收回来"这一步由 `list_pages` 决定翻几页。

| 上限 | 管哪一段 | 超了会怎样 | 调它会影响 |
|---|---|---|---|
| `--search-limit` | 检索收集候选（含站内检索翻页） | 停止继续翻页，已收下的保留 | 单源耗时、内存 |
| `--list-pages` | 回退列表页翻几页 | 只影响回退路径的候选条数 | 回退源耗时 |
| `--fetch-limit` / `--limit` | 抓多少篇详情 + 截取多少候选 | 只抓前 N 条候选 | 详情抓取耗时、入库量 |

`--limit` **不再是**"取够就提前停"的开关：改前 `--limit 3` 在**检索收集阶段**就 break，
所以站内 430 条的源也只拿到 3 条候选；现在收集与抓取分开，`--limit 3` = 收全部命中、只抓 3 篇详情。


## 采集模式：检索优先 → 列表页兜底

每个源先看 `sources.json` 里自己的 `search.type`：

| `search.type` | 做法 |
|---|---|
| `api` | 直接请求站点的检索 JSON 接口（GET 或 POST + JSON body + 自定义头） |
| `results_page` | 请求"结果页"HTML，按 `list_selector` + `link_re` 抽链接（scspi） |
| `browser` | 用隐身浏览器打开页面/在页面里 fetch 取数接口（xinhua 系；静态直调 403） |
| `none` | 确认没有可用站内检索，直接走列表页 |

- 检索**取到候选**就用检索结果，报告标 `api(...)` / `results_page` / `browser`，记录的 `match_level` 记 `server`
  （服务端已按检索词筛过，不再本地筛一遍）。
- 检索**不可用/报错/0 条**时自动回退该源 `url` 的列表页，报告标 `…→回退列表页`，状态记「回退列表页」，
  `·` 行保留回退原因；回退路径按 `global + per_source` 在标题+正文里本地过滤，`match_level` 记
  `title` / `content` / `title+content`。
- **两条路都失败**才算该源失败（状态「失败」，进程退出码 1）。

**词表口径（两张表各管一段）**：

| 表 | 作用 | 什么时候用 |
|---|---|---|
| `keywords.global`（`--keyword` 覆盖它） | 既是**检索词**（每个源把每个词各搜一遍），也是**回退过滤词** | 两种模式都用，口径一致 |
| `keywords.per_source[key]` | **只在回退列表页时追加**到过滤词，不改检索词 | 仅回退路径 |

所以：检索源的 `matched_keywords` 记服务端实际用的那个 `search_keyword`（来自 `global`），
回退源的 `matched_keywords` 记本地命中的词（可能来自 `global`，也可能来自 `per_source`）——
日志里命中词后面会标 `(global)` / `(per_source)`，一眼能看出这条是「按检索词留的」还是
「按本源补充词留的」。

## 每源报告与四个计数

```
[人民网/people] 检索方式=api(POST) 状态=成功
    站内总数=南海:10053 仁爱礁:2490  收集候选=63  详情抓取=3  入库=3  错误=0  耗时 10.6s
    检索=api 页数=5｜站内总数=南海:10053 仁爱礁:2490｜收集候选=63｜HTTP=200
    ✓ [人民网] 生态环境部就南海问题答记者问 保留 检索词=南海(global，服务端筛)
    ✓ [中国海警局] 中国海警位黄岩岛领海执法巡查 保留 命中=[管控(per_source)]
    - [中国海警局] 某条无关新闻 丢弃原因=未命中过滤词 查看词=[南海, 仁爱礁, 维权执法…另4个] 命中=[]
    ✓ [央视网] 某条新闻 保留（--no-filter 未做过滤，查看词=[南海, 仁爱礁, 军事, 国防…另2个]）

[海警局·维权执法/ccg_wqzf] 检索方式=无站内检索→回退列表页 状态=回退列表页
    站内总数=列表(未检索)=58  收集候选=58  详情抓取=5  入库=5  错误=0  耗时 5.4s
    收集候选=58｜列表翻页=3 页（请求 3 页 / 候选 58 条）列表引擎=static｜过滤词=[…]｜HTTP=200
```

- **站内总数**：服务端自己报的总命中（检索源才有，如 `南海:10053`）；回退源没有这个数字，
  如实标成 `列表(未检索)=M`，不拿候选数冒充总数。
- **收集候选**：**本轮实际取回的候选条数**（去重后）。改前叫「检索命中」，容易被读成"站内就这么多"——
  现在与站内总数并列，一眼能看出收集是否被上限截断。
- **详情抓取数**：真正抓到详情页的篇数（含后来被 `min_body_length` 丢掉、被关键词筛掉的）。
- **入库数**：`upsert_records` 返回的 `inserted + updated`；未写库时的三种标记分别是
  `0(未入库)`（`--no-db`/`--list-only`）、`0(未就绪)`（`db.py` 导入失败）、`0(连接失败)`
  （`db.py` 能导入但连不上库，最常见的是忘了 `--with pymysql`）。注意启动横幅的
  `入库：已就绪（库 newscrawl）` 只表示 **`db.py` 模块可用**，连接失败会在随后单独打一行 `⚠ 入库：连接失败：…（本轮跳过入库）`。
- **错误数**：真实失败数（网络/解析/入库）。配置里标注"已知不可用"的检索不算错误，只在 `·` 行注明原因。
- 回退源的详情行里额外给 `列表翻页=N 页（请求 M 页 / 候选 K 条）`与 `过滤词=[…]`；
  推不出分页规律时 `·` 行写「列表翻页=1 页（未翻页：…）」。


### 每条记录的保留/丢弃日志

每条详情页都会打一行，措辞按 `keep` 而不是按"命中数"决定，避免「留下了却打印未命中」：

```
    ✓ [人民网] 生态环境部就南海问题答记者问 保留 检索词=南海(global，服务端筛)
    ✓ [中国海警局] 中国海警位黄岩岛领海执法巡查 保留 命中=[管控(per_source)]
    - [中国海警局] 某条无关新闻 丢弃原因=未命中过滤词 查看词=[南海, 仁爱礁, 维权执法…另4个] 命中=[]
    - [中国海警局] 某条招聘公告 丢弃原因=命中排除词(招聘) 查看词=[南海, 仁爱礁, 维权执法…另4个]
    ✓ [央视网] 某条新闻 保留（--no-filter 未做过滤，查看词=[南海, 仁爱礁, 军事, 国防…另2个]）
```

- `✓` = 保留（并给出依据：检索源给服务端用的检索词，回退源给本地命中词及来源）；
- `-` = 丢弃（给出丢弃原因 + 查了哪些词 + 命中了哪些词）；
- 命中词标 `(global)` / `(per_source)`，能看出是「按检索词留的」还是「按本源补充词留的」；
- `--no-filter` 下不会出现「未命中」「丢弃」字样，只写「保留（--no-filter 未做过滤）」。

### 状态口径

| 状态 | 含义 |
|---|---|
| `成功` | 走检索路径且取到候选（回退、错误都没有） |
| `部分成功` | 取到候选了，但详情抓取/入库出现过错误 |
| `检索无结果` | 走检索路径、`收集候选=0` 且**没有回退**。注意：现有 `collect_candidates()` 在检索 0 条时**一定会去回退**，所以这一档只在"检索 0 条 + 列表页成功"以外的路径上出现（`--list-only` 实测仍会回退）；真实源目前都会落到「回退列表页」 |
| `回退列表页` | 走了列表页兜底；`·` 行里保留回退原因（检索不可用/报错/0 条） |
| `失败` | 检索与列表页两条路都没取到候选（进程退出码 1） |

末尾汇总表列出全部源的同名列，再给「共保留 N 篇；失败源 …」；若有「检索无结果」的源会另起一行列出。
`失败源 N 个` 只统计「失败」，新状态不会被算进去。

## 入库（`gov_news/db.py`）

`crawl.py` 延迟导入同一目录下的 `db.py`，按下面契约调用（字段名以 db.py 为准）：

```python
from db import connect, ensure_schema, upsert_records, load_records_from_json
conn = connect(db_name="newscrawl")
ensure_schema(conn, source_key, source_name)
upsert_records(conn, source_key, source_name, records, batch_time)   # -> {"inserted","updated","skipped","errors"}
load_records_from_json(conn, path, batch_time=...)                   # --from-json 用
```

- **同一轮共用一个 `batch_time`**（启动时间，`--dump-records` 的 `meta.batch_time` 里也能看到）。
- 一条记录的关键字段：`title` / `list_title` / `body` / `body_length` / `published` / `summary` / `category` /
  `search_keyword` / `matched_keywords` / `match_level` / `image_urls`（正文前 3 张图片的**外链地址**，不下载）/
  `http_status` / `fetched_at` / `_body_source`。
- `db.py` 不存在时：**入库路径明确报「db.py 尚未就绪」并跳过**，检索/报告/导出照常跑（退出码只看源失败）；
  `--from-json` 因为只做入库，会直接报错并以退出码 2 结束。

## 产物与回灌

- 默认**不写 `out/*.json`**，结果只入库。
- `--dump-records PATH` 才会导出 JSON（结构 `{"meta": {...}, "articles": [...]}`，`meta.per_source_stats`
  里就是报告里的四个计数），导出的文件可以直接喂给 `--from-json`。

## `sources.json` 的 `search` 配置块

| 字段 | 说明 |
|---|---|
| `type` | `api` / `results_page` / `browser` / `none` |
| `method` | `api` 型用 `GET` 或 `POST`（people 是 POST） |
| `url` / `url_template` | 接口地址 / 结果页与检索页模板，支持 `{keyword}`、`{page}`（`{keyword}` 自动 percent-encode） |
| `params` / `body` | GET 查询参数 / POST 的 JSON body，值里同样支持 `{keyword}`、`{page}` |
| `headers` | 自定义请求头（people 需要 `Origin` / `Referer`） |
| `pages` / `page_start` | 每个词翻几页（默认 5、上限 20）/ 页码起点 |
| `items_path` / `total_path` | JSON 里结果数组与总数的路径 |
| `title_field` / `url_field` / `date_field` / `summary_field` / `category_field` | 字段映射 |
| `link_re` / `url_prefixes` | 检索后的**二次过滤**：频道型源靠它收窄（`mod_qwfb`→`/gfbw/`、`people_mil`→`military.` 域名、xinhua 两频道→URL 前缀） |
| `proxy` | 该源是否需要代理（只有 scspi 为 `true`；地址由 `--proxy` 提供） |
| `js_fetch.url_template` | `browser` 型专用：先打开 `url_template` 页面，再**在页面上下文里 `fetch`** 这个地址（xinhua 系） |
| `xhr_url_re` | `browser` 型备选：从会话录到的 XHR 里按正则找最近一条 JSON 响应 |
| `page_action` | `browser` 型备选：`{fill_selector, press, wait_ms, after_ms}`，填检索框后回车 |
| `unavailable` / `unavailable_reason` | 调研已确认取不到结果：配置留档，运行时直接回退列表页并在报告里注明原因 |
| `verify` | 检索请求是否校验证书（mod / mil81 必须 `false`） |
| `note` | 本源的检索坑 |

列表页兜底与详情页仍用源上的老字段：`url` / `engine` / `verify` / `list_selector` / `link_re` /
`title_selector` / `body_selector` / `limit` / `min_body_length`。
`detail_engine` / `detail_verify` 单独指定详情页引擎（不写则用 `engine` / `verify`）。

回退列表页翻页有两个字段（都是可选的）：

| 字段 | 说明 |
|---|---|
| `list_pages` | 本源回退时最多翻几页；不写则取 `settings.json` 的 `list_pages`，`--list-pages` 可临时覆盖 |
| `page_url_template` | 分页 URL 模板，含 `{page}`。写完整 URL 或只写文件名都行（`index_{page}.html` 会拼到源 `url` 的目录上）。不写则从首页 HTML 里**实测推导**分页链接，推不出来就只抓第 1 页并在报告里写「未翻页」 |

翻页行为：**配了 `page_url_template` 时第 1 页也走模板**（所以 ccg_wqzf 的第 1 页是 `index_1.html`，
不是 `/wqzf/` 目录页）；没配模板才先抓源 `url` 再推模板。逐页请求，**URL + 归一化标题去重后合并**；
单页失败只记错误不中断该源；分页请求返回 **404 视为已到最后一页**（`ccg_wqzf` 第 24 页起就是 404），
记成 `·` 说明而不是错误。

模板推导的证据门槛（三条缺一不可，就是为了不把文章链接误当分页）：

1. 至少 **2 条**同站链接能归到同一个模板（把文件名里的数字换成 `{page}` 后完全相同）；
2. 这些链接的页码里出现 `1` 或 `2`；
3. 页码取文件名里**最后一段**数字，且文件名主干不能整个就是页码 —— `index_{page}.html` 算，
   `{page}.html` 不算。

实测反例（2026-11-20）：`nanhai` 首页第一个带数字的文件名是文章链接
`/info-detail/21/18489.html`，只按"第一条"推会得出 `/info-detail/21/{page}.html`，
翻页翻出来是 1.html/2.html 这种空页（实测 HTTP 200 但候选 0）；加上门槛后 `nanhai` 正确地
报「未翻页」。`cctv` / `xinhua_mil` 同样推不出分页（列表页是单页到底），只有显式配了模板的
`ccg_wqzf` 会翻。


## `keywords.json`

- `global`：**既是本地过滤词，也是站内检索的统一词表**（一个源把每个词各搜一遍）；
  `--keyword` 覆盖它时两者一起换。检索模式与回退模式共用这一份，口径一致。
- `per_source`：只在**回退列表页**时**追加**到过滤词（不改检索词，检索路径由服务端筛）。
  命中来源会在日志里标 `(global)` / `(per_source)`。
- `exclude` / `match`：同以前（命中排除词即丢弃；`all` 表示必须全部命中）。

## 各源检索方式与实测读数（2026-11-20）

| 源 | 检索方式 | 实测读数 | 说明 |
|---|---|---|---|
| `mod` 国防部网 | `api` GET | 200，站内 南海=430 / 黄岩岛=81 | `mod-search.mod.gov.cn`、`channelId=718`、`verify=false` |
| `mod_qwfb` 记者会专题 | `api` GET | 同 mod（复用 718） | 检索后按 `/gfbw/` 前缀过滤 |
| `mil81` 中国军网 | `api` GET | 200，站内 南海=566 | 自有后端 `mil-search.81.cn`、`channelId=636`；**返回的是 `www.81.cn`**，`link_re` 同时容纳两者 |
| `people` 人民网 | `api` POST | 200，站内 南海=10052 | body 里 `hasTitle=true`；带 `Origin`/`Referer`；标题摘要带 `<em>` → `strip_html` |
| `people_mil` 人民网·军事 | `api` POST | 同上（复用 people） | 按 `military.people.com.cn` 域名过滤 |
| `scspi` 南海战略态势感知 | `results_page` GET | **不加代理：SSLError/超时失败；加代理：200、命中 3 条** | 唯一需要 `--proxy` 的源 |
| `xinhua` 新华网 | `browser` + 页面内 `fetch` | 200，站内 南海=5686；2 页取回 12 条 | 取数接口静态直调恒 403，只能页面里发；偶发 503 限流 |
| `xinhua_mil` 新华网·军事 | `browser` + 前缀过滤 | 5 页×2 词约 100 条里 `/milpro/` **命中 0** | 多走回退列表页（列表候选 106） |
| `xinhua_world` 新华网·国际 | `browser` + 前缀过滤 | 2 页×2 词未命中 `/world/`；单源耗时 **38.0s → 24.0s** | 见「xinhua 系耗时」 |
| `cctv` 央视网 | `browser`（留档不可用） | 结果列表取不到 | 回退列表页（候选 84） |
| `fmprc` 外交部 | `browser`（留档不可用） | IRS 检索页 0 条、接口缺凭证 | 回退列表页（候选 63） |
| `ccg` 中国海警局 | `none` | — | 跳转页结果恒 0、后端 404；回退列表页（候选 11） |
| `ccg_wqzf` 海警·维权执法 | `none` | — | 回退列表页 + 翻页：`index_1.html`=29 条候选、`index_2.html`=29 条、`index_23.html`=19 条（2026-11-20 实测）；`list_pages=1`→29 条、`list_pages=3`→49 条（改前只抓目录页 19 条） |
| `nanhai` 中国南海研究院 | `none` | — | 表单是空壳；回退列表页（候选 8） |
| `mod_search_scs` | — | 已 `enabled=false`、`deprecated` | 最早的"检索接口当列表页"实验，已被 `mod` 的通用检索取代，条目与参数保留供追溯 |

## xinhua 系耗时（2026-11-20 实测）

全量联调里 `xinhua_world` 单源 38.0s（同批其他源 3–19s），逐段量过（脚本在 `gov_news/out/measure_xinhua_*.py`）：

| 量法 | 读数 | 结论 |
|---|---|---|
| 每个 (词, 页) 重新 `goto https://so.news.cn/` 一次（`network_idle=True`） | 4 页合计 22.02s（单页 5.3–7.0s） | 单一导航 **5–7s**，**是主要构成** |
| 同上但 `network_idle=False` | 4 页合计 25.95s | 关掉 `network_idle` **不提速**（反而更慢），所以不动它 |
| 一次导航 + 页内连续 `fetch` curPage=1/2/3 | 导航 1.83s + 三次 fetch 1.42/1.08/1.71s = **6.04s**，每页仍是 10 条、页码顺序正确 | 检索页固定时没有理由反复导航 |
| 详情页 `fetch()`（静态 200，真实 URL） | 0.19s / 0.22s，一次慢到 4.17s | 详情**不是**主要构成 |

据此改成：**检索页不变（只有 `curPage` 变）时复用同一页面做页内 fetch**，页面被关掉/崩掉才重新导航
（`reuse_page()` 会先探活，失败则重新 `goto` 一次再试）；**不改 `network_idle`、不缩超时、不减词/页**。

`xinhua_world` 单源耗时（`--sources xinhua_world --fetch-limit 3`，同一台机）：**38.0s → 24.0s（检索通、命中 1 条）**；
撞上 `so.news.cn` 偶发 503 时是 **22.0s（检索 503 → 回退列表页、抓 3 条详情）**——503 是站点侧风控，
与本改动无关，两种情况下都不再是 30s+。

## 复跑用的核对脚本（都在 `gov_news/out/`，该目录不入库）

`out/` 下的 `measure_*.py` / `dev_selftest_*.py` 是排查与验收用的只读脚本，改完 `crawl.py` 想复核时直接跑：

```bash
uv run --isolated --extra ai python gov_news/out/dev_selftest_status.py       # 状态映射 + 关键词过滤 + 日志措辞（离线）
uv run --isolated --extra ai python gov_news/out/dev_selftest_status_cli.py   # 伪造"检索 0 条"的源，看报告/汇总怎么标
uv run --isolated --extra ai python gov_news/out/probe_list_pagination.py --source ccg_wqzf   # 实测某源的分页规律（只读）
uv run --isolated --extra ai python gov_news/out/probe_list_pagination.py --source xinhua_mil
uv run --isolated --extra ai python gov_news/out/probe_derive_template.py --source nanhai     # 看代码实际推导出的分页模板与试抓结果
uv run --isolated --extra ai python gov_news/out/probe_reachability.py https://www.ccg.gov.cn/wqzf/index_1.html  # 纯探活，区分"代码问题"与"站点抽风"
uv run --isolated --extra ai python gov_news/out/verify_settings_default.py   # 证明"CLI 未传时读了 settings.json"
uv run --isolated --extra ai python gov_news/out/verify_candidate_guard.py    # 离线验证 5000 护栏与 search_limit 精确性（不联网）
uv run --isolated --extra ai python gov_news/out/measure_xinhua_timing.py --pages 2 --keywords 南海,仁爱礁   # network_idle 开关对比
uv run --isolated --extra ai python gov_news/out/measure_xinhua_reuse.py      # 一次导航 vs 每次导航
uv run --isolated --extra ai python gov_news/out/measure_xinhua_phases.py     # 检索/详情分段耗时
uv run --isolated --extra ai python gov_news/out/measure_detail_cost.py       # 详情页逐条耗时（用真实检索结果里的 URL）
```

`verify_settings_default.py` 会**临时改写 `settings.json` 再恢复**（脚本内 `finally` 恢复），
跑它之前别同时跑别的采集进程。

## 代理与证书纪律

- `http://127.0.0.1:7897` **只给需要翻墙的站点**；本清单一律不写死进代码，只由 `--proxy` 传入。
- 14 个国内源**全部直连**；只有 `scspi` 配了 `search.proxy=true`（实测不加代理 3/3 失败）。
- `mod` / `mod_qwfb` / `mil81` 的证书与域名不匹配（`CN=*.unionpayintl.com`），必须 `verify=false`；
  详情页走 `detail_verify` 覆盖。

## 坑与已知限制

1. **`people` 的 `<em>` 高亮**：检索接口返回的 `title` 和 `content` 里带 `<em>南海</em>`，标题/摘要入库前
   都走 `strip_html()`（实测导出里 `em=False`），关键词匹配与 `match_level` 也用的是清洗后的文本。
2. **`cctv` 的 `link_re` 必须大小写都收**：实际链接形如
   `https://tv.cctv.com/2026/11/19/VIDEjRRYczjjaRgmVYUa7Zfj261119.shtml`，只写 `VIDE` 或只写 `VID[A-Z0-9]+`
   都会漏，现为 `VID[A-Za-z0-9]+`（改前实测候选 0，改后 84）。
3. **`xinhua` 检索页是 hash 路由**：直接改 hash 属于"同文档导航"，Playwright 拿不到新响应（报
   `Failed to get response`），因此实现改成"每页重开基础页 + 在页面里 fetch 取数接口"。
4. **`so.news.cn/getNews` 会偶发 503**：连续多页/多源调用时出现（调大 `--delay` 只能缓解），
   该页按错误记入并继续，整源仍可能回退列表页。
5. **`mil81` 详情页 `h1` 常缺**：标题会退到 `<title>`，带上 `- 中国军网` 后缀（去重用的 `title_key` 会剥掉）。
6. **图片只存前 3 张外链**：在正文容器里按出现顺序取绝对 `http(s)` 地址，站标/导航图标因为不在正文容器里
   已被排除，但不下载、不校验可用性。
7. **回退列表页只翻到 `list_pages` 为止**：`ccg_wqzf` 实测有 23 页（`index_1.html`…`index_23.html`，
   `index_24.html` 起 404），默认 `list_pages=3` 只取前 3 页；要全量就把 `--list-pages` 或该源的
   `list_pages` 调大（上限 50）。翻页推不出来（JS 分页/单页到底）的源只抓第 1 页，报告里标「未翻页」。
8. **`mod-search.mod.gov.cn` 连续请求会偶发返回空 body**：表现为 `站内检索(api)失败
   JSONDecodeError: Expecting value: line 1 column 1`，隔几十秒再跑即恢复（2026-11-20 实测复现两次）。
   这是站点侧限流、不是代码问题：该源此时会照常回退列表页（候选 41 条），报告里 `!` 行写明原因。
   短时间反复跑同一个源时把 `--delay` 调大或错开时间。
9. **`--search-limit 0` 在 settings 里才是"不设上限"**：CLI 的 `--search-limit` 只接受 0（=用 settings）
   或正数，负数会被参数校验拒绝，避免把"不设上限"这种危险开关做成一行命令就能打开。
10. **数值型参数用 0 表示"没传"**：`--fetch-limit` / `--search-limit` / `--list-pages` / `--delay` /
    `--timeout` 的 0 都是"用 settings/源配置"，所以**没法用命令行把某一项显式设成 0**
    （比如"这轮一篇详情都不抓"）。真要那样做就换一个 `--settings` 文件把 `fetch_limit` 写成 0。
11. **`settings.json` 存在但读不了会大声告警**：文件不存在只提示一句继续跑（走源配置/内置默认）；
    但文件存在而 JSON 坏了（括号不配对、带了 UTF-8 BOM 等）会打印 `⚠ settings 文件存在但读不了`
    两行并说明"本轮全部走源配置/内置默认"，不会静默套用默认值。配置读取统一用 `utf-8-sig`，
    所以 Windows 记事本另存为 UTF-8（带 BOM）也能读。
8. **`--delay` 下限 1s**：低于 1 会被参数校验拒绝（对国内站点的礼貌间隔）。

## 加一个源（改 JSON 就行）

```json
{
  "key": "example", "name": "某部门官网", "url": "https://www.example.gov.cn/",
  "enabled": true, "engine": "static", "verify": true,
  "list_selector": "ul.news-list li a",
  "link_re": "example\\.gov\\.cn/.+\\d{6}\\.html$",
  "title_selector": "h1", "body_selector": ".article-content", "limit": 5,
  "search": {
    "type": "api", "method": "GET",
    "url": "https://so.example.gov.cn/api/search",
    "params": {"pageNumber": "{page}", "pageSize": 10, "title": "{keyword}"},
    "pages": 5, "page_start": 1,
    "items_path": "data.list", "total_path": "data.total",
    "title_field": "title", "url_field": "url", "date_field": "publishTime",
    "link_re": "example\\.gov\\.cn/.+\\d{6}\\.html$",
    "note": "怎么找到这个接口的，写在这里"
  },
  "note": "为什么这么配，写在这里"
}
```

找不到可用接口时：`search.type` 写 `none`（走列表页），或用 `browser` + `js_fetch` / `xhr_url_re` /
`dom_link_selector` 其中之一；确认站点没有检索能力就把原因写进 `search.note`。

回退源要翻页就在条目上加 `list_pages` 与 `page_url_template`（见上文「回退列表页翻页」）：

```json
{
  "key": "ccg_wqzf", "url": "https://www.ccg.gov.cn/wqzf/",
  "list_pages": 3,
  "page_url_template": "https://www.ccg.gov.cn/wqzf/index_{page}.html"
}
```

